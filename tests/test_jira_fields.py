"""Standard Jira fields: reading them, editing, creating issues, transitions and links."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import JsonValue

from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import FieldChanges, JiraClient, normalize_project_key
from productivity_agent_tools.jira.fields import check_label, due_date, parse_editmeta
from productivity_agent_tools.server import Connected, build_server
from tests.conftest import EDITMETA, FakeJira

API = "/jira/rest/api/2"


def _structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    return dict(result.structured_content)


def _sent(fake: FakeJira) -> list[tuple[str, str, Any]]:
    return [
        (r.method, r.url.path.removeprefix(API), json.loads(r.content) if r.content else None)
        for r in fake.writes()
    ]


async def test_issue_detail_has_the_standard_fields(make_jira: Callable[..., JiraClient]) -> None:
    issue = await make_jira().get_issue("ABC-1")
    assert (issue.project, issue.due_date, issue.resolution) == ("ABC", "2026-11-30", None)
    assert (issue.components, issue.fix_versions, issue.affects_versions) == (["API"], ["1.0"], [])
    assert (issue.original_estimate, issue.remaining_estimate, issue.time_spent) == (
        "2d",
        "1d",
        "1d",
    )
    assert (issue.original_estimate_seconds, issue.time_spent_seconds) == (57600, 28800)
    blocks, relates, odd = issue.links
    assert (blocks.relation, blocks.key, blocks.summary, blocks.status, blocks.link_id) == (
        "blocks",
        "ABC-2",
        "Ship it",
        "To Do",
        "900",
    )
    assert (relates.relation, relates.key, relates.summary) == ("relates to", "XYZ-9", None)
    assert (odd.relation, odd.key) == ("Odd", "XYZ-10")


def test_field_metadata_is_read_defensively() -> None:
    fields = parse_editmeta(EDITMETA)
    assert "broken" not in fields
    labels = fields["labels"].info()
    assert (labels.type, labels.operations, labels.required) == (
        "array of string",
        ["add", "set", "remove"],
        False,
    )
    assert fields["options"].info().allowed_values == ["Red", "k"]
    assert parse_editmeta({"fields": "nope"}) == {} == parse_editmeta(None)
    values: list[JsonValue] = [{"name": f"v{n}"} for n in range(60)]
    many: JsonValue = {"fields": {"f": {"allowedValues": values}}}
    info = parse_editmeta(many)["f"].info()
    assert (len(info.allowed_values), info.more_values, info.name) == (50, 10, "f")


@pytest.mark.parametrize("bad", ["", "two words", " "])
def test_labels_are_single_words(bad: str) -> None:
    with pytest.raises(AtlassianError, match="single words"):
        check_label(bad)


def test_due_dates_are_dates_or_cleared() -> None:
    assert due_date(" 2026-11-30 ") == "2026-11-30"
    assert due_date("None") is None
    with pytest.raises(AtlassianError, match="not a date"):
        due_date("next friday")


def test_project_keys_are_validated() -> None:
    assert normalize_project_key(" abc ") == "ABC"
    with pytest.raises(AtlassianError, match="not a valid project key"):
        normalize_project_key("A")


async def test_edit_fields_lists_the_screen(make_jira: Callable[..., JiraClient]) -> None:
    result = await make_jira().edit_fields("abc-1")
    by_id = {f.id: f for f in result.fields}
    assert result.issue_key == "ABC-1"
    assert by_id["summary"].required is True
    assert by_id["priority"].allowed_values == ["High", "Medium"]


async def test_edit_sets_values_and_adds_or_removes_list_items(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    changed, outcome = await make_jira(dry_run=False).edit_issue(
        "ABC-1",
        FieldChanges(
            summary=" New title ",
            description="h3. Why",
            priority="high",
            reporter="kim",
            due_date="none",
            original_estimate="2 D",
            remaining_estimate="4h",
            add_labels=["needs-review"],
            remove_labels=["ops"],
            add_components=["web"],
            remove_components=["API"],
            add_fix_versions=["1.1"],
            remove_affects_versions=["1.0"],
        ),
    )
    assert outcome.sent is True
    assert changed == [
        "Summary",
        "Description",
        "Priority",
        "Reporter",
        "Due Date",
        "Time Tracking",
        "Labels",
        "Component/s",
        "Fix Version/s",
        "Affects Version/s",
    ]
    ((method, path, body),) = _sent(fake)
    assert (method, path) == ("PUT", "/issue/ABC-1")
    assert body == {
        "fields": {
            "summary": "New title",
            "description": "h3. Why",
            "priority": {"id": "2"},
            "reporter": {"name": "kim"},
            "duedate": None,
            "timetracking": {"originalEstimate": "2d", "remainingEstimate": "4h"},
        },
        "update": {
            "labels": [{"add": "needs-review"}, {"remove": "ops"}],
            "components": [{"add": {"id": "12"}}, {"remove": {"id": "10"}}],
            "fixVersions": [{"add": {"id": "21"}}],
            "versions": [{"remove": {"id": "20"}}],
        },
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (FieldChanges(), "Nothing to change"),
        (FieldChanges(summary="  "), "must not be empty"),
        (FieldChanges(issue_type="Bug"), "'issuetype' is not on the edit screen of ABC-1"),
        (FieldChanges(priority="Urgent"), r"'Urgent' is not a valid Priority\. Allowed: High"),
        (FieldChanges(add_labels=["a b"]), "single words"),
        (FieldChanges(reporter="none"), "Nobody matching 'none'"),
        (FieldChanges(reporter="  "), "A person must be named"),
        (FieldChanges(original_estimate="soon"), "not a Jira duration"),
    ],
)
async def test_bad_edits_are_refused_before_sending(
    fake: FakeJira, make_jira: Callable[..., JiraClient], changes: FieldChanges, message: str
) -> None:
    with pytest.raises(AtlassianError, match=message):
        await make_jira(dry_run=False).edit_issue("ABC-1", changes)
    assert fake.writes() == []


async def test_values_without_an_id_are_sent_by_name(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    meta = {**EDITMETA["fields"], "priority": {"allowedValues": [{"name": "High"}]}}
    fake.overrides[f"{API}/issue/ABC-1/editmeta"] = httpx.Response(200, json={"fields": meta})
    await make_jira(dry_run=False).edit_issue("ABC-1", FieldChanges(priority="HIGH"))
    assert _sent(fake)[0][2] == {"fields": {"priority": {"name": "High"}}}
    await make_jira(dry_run=False).edit_issue("ABC-1", FieldChanges(add_labels=["x"]))
    assert _sent(fake)[1][2] == {"update": {"labels": [{"add": "x"}]}}


async def test_create_fields_list_types_then_one_types_fields(
    make_jira: Callable[..., JiraClient],
) -> None:
    types_only = await make_jira().create_fields("abc")
    assert [(t.name, t.subtask) for t in types_only.issue_types] == [
        ("Bug", False),
        ("Task", False),
        ("Sub-task", True),
    ]
    assert (types_only.issue_type, types_only.fields) == (None, [])
    task = await make_jira().create_fields("ABC", "task")
    by_id = {f.id: f for f in task.fields}
    assert task.issue_type == "Task"
    assert (by_id["summary"].required, by_id["priority"].required) == (True, False)
    with pytest.raises(AtlassianError, match="Available: Bug, Task, Sub-task"):
        await make_jira().create_fields("ABC", "Epic")


async def test_create_sends_project_type_and_fields(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    chosen, outcome = await make_jira(dry_run=False).create_issue(
        "abc",
        "Task",
        FieldChanges(
            summary="Write docs",
            description="See *notes*",
            priority="High",
            add_labels=["docs"],
            add_components=["api"],
        ),
        assignee="me",
    )
    assert (chosen.name, outcome.sent) == ("Task", True)
    ((method, path, body),) = _sent(fake)
    assert (method, path) == ("POST", "/issue")
    assert body == {
        "fields": {
            "summary": "Write docs",
            "description": "See *notes*",
            "priority": {"id": "2"},
            "project": {"key": "ABC"},
            "issuetype": {"id": "3"},
            "labels": ["docs"],
            "components": [{"id": "10"}],
            "assignee": {"name": "pat"},
        }
    }
    search = [r for r in fake.requests if r.url.path.endswith("/createmeta/ABC/issuetypes/3")]
    assert search


async def test_subtasks_need_a_parent_and_others_must_not_have_one(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    with pytest.raises(AtlassianError, match="sub-task type, so give the parent"):
        await jira.create_issue("ABC", "Sub-task", FieldChanges(summary="x"))
    with pytest.raises(AtlassianError, match="not a sub-task type, so leave parent out"):
        await jira.create_issue("ABC", "Task", FieldChanges(summary="x"), parent="ABC-1")
    await jira.create_issue("ABC", "sub-task", FieldChanges(summary="x"), parent="abc-1")
    assert _sent(fake)[0][2]["fields"]["parent"] == {"key": "ABC-1"}


async def test_create_refuses_missing_required_fields_and_unknown_ones(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    with pytest.raises(AtlassianError, match=r"also needs: Severity \(customfield_100\)"):
        await jira.create_issue("ABC", "Bug", FieldChanges(summary="x"))
    with pytest.raises(AtlassianError, match="needs a summary"):
        await jira.create_issue("ABC", "Task", FieldChanges(summary=" "))
    with pytest.raises(AtlassianError, match="'duedate' is not on the create screen of Task"):
        await jira.create_issue("ABC", "Task", FieldChanges(summary="x", due_date="2026-12-01"))
    assert fake.writes() == []


async def test_transitions_offer_resolutions(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    available = {t.name: t for t in await jira.list_transitions("ABC-1")}
    assert fake.requests[0].url.params["expand"] == "transitions.fields"
    assert (available["Done"].resolutions, available["Done"].resolution_required) == (
        ["Fixed", "Won't Do"],
        False,
    )
    assert available["Start Progress"].resolutions == []

    await jira.transition_issue("ABC-1", "done", "fixed")
    assert _sent(fake)[-1][2] == {
        "transition": {"id": "21"},
        "fields": {"resolution": {"name": "Fixed"}},
    }
    with pytest.raises(AtlassianError, match="Allowed: Fixed, Won't Do"):
        await jira.transition_issue("ABC-1", "Done", "Duplicate")
    with pytest.raises(AtlassianError, match="does not set a resolution"):
        await jira.transition_issue("ABC-1", "Start Progress", "Fixed")


async def test_a_required_resolution_must_be_given(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    required = {
        "transitions": [
            {
                "id": "21",
                "name": "Done",
                "fields": {"resolution": {"required": True, "allowedValues": [{"name": "Fixed"}]}},
            }
        ]
    }
    fake.overrides[f"{API}/issue/ABC-1/transitions"] = httpx.Response(200, json=required)
    with pytest.raises(AtlassianError, match=r"needs a resolution\. Ask the user which one: Fixed"):
        await make_jira().transition_issue("ABC-1", "Done")


@pytest.mark.parametrize(
    ("relation", "inward", "outward", "sentence"),
    [
        ("blocks", "ABC-1", "ABC-2", "ABC-1 blocks ABC-2"),
        (" Blocks ", "ABC-1", "ABC-2", "ABC-1 blocks ABC-2"),
        ("is  blocked by", "ABC-2", "ABC-1", "ABC-2 blocks ABC-1"),
        ("duplicates", "ABC-1", "ABC-2", "ABC-1 duplicates ABC-2"),
    ],
)
async def test_links_read_as_a_sentence(
    fake: FakeJira,
    make_jira: Callable[..., JiraClient],
    relation: str,
    inward: str,
    outward: str,
    sentence: str,
) -> None:
    said, outcome = await make_jira(dry_run=False).link_issues("abc-1", relation, "ABC-2")
    assert (said, outcome.sent) == (sentence, True)
    body = _sent(fake)[0][2]
    assert (body["inwardIssue"], body["outwardIssue"]) == ({"key": inward}, {"key": outward})
    assert "comment" not in body


async def test_bad_links_are_refused(fake: FakeJira, make_jira: Callable[..., JiraClient]) -> None:
    jira = make_jira(dry_run=False)
    with pytest.raises(AtlassianError, match="cannot be linked to itself"):
        await jira.link_issues("ABC-1", "blocks", "abc-1")
    with pytest.raises(AtlassianError, match="Use one of: blocks / is blocked by; duplicates"):
        await jira.link_issues("ABC-1", "causes", "ABC-2")
    assert fake.writes() == []
    await jira.link_issues("ABC-1", "blocks", "ABC-2", "because *reasons*")
    assert _sent(fake)[0][2]["comment"] == {"body": "because *reasons*"}


async def test_field_tools_end_to_end(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_settings: Callable[..., Settings],
) -> None:
    dry = build_server(make_settings(), {"jira": Connected(make_core(), "test")})
    edit = {"key": "ABC-1", "priority": "High", "add_labels": ["x"]}
    blocked = _structured(await dry.call_tool("jira_edit_issue", edit))
    assert (blocked["sent"], blocked["request"]["method"]) == (False, "PUT")
    new = {"project": "ABC", "issue_type": "Task", "summary": "Write docs"}
    planned = _structured(await dry.call_tool("jira_create_issue", new))
    assert (planned["issue_key"], planned["created_id"]) == ("ABC (new)", None)
    assert fake.writes() == []

    live = build_server(
        make_settings(dry_run=False), {"jira": Connected(make_core(dry_run=False), "test")}
    )
    first = _structured(await live.call_tool("jira_edit_issue", edit))
    again = _structured(await live.call_tool("jira_edit_issue", edit))
    assert first["message"] == "Updated ABC-1: Priority, Labels."
    assert again["repeat_suppressed"] is True
    created = _structured(await live.call_tool("jira_create_issue", new))
    assert (created["issue_key"], created["created_id"]) == ("ABC-2", "ABC-2")
    assert created["message"] == "Created Task ABC-2."
    linked = _structured(
        await live.call_tool(
            "jira_link_issues", {"key": "ABC-1", "relation": "blocks", "other_key": "ABC-2"}
        )
    )
    assert linked["message"] == "Linked: ABC-1 blocks ABC-2."
    moved = _structured(
        await live.call_tool(
            "jira_transition_issue", {"key": "ABC-1", "transition": "Done", "resolution": "Fixed"}
        )
    )
    assert moved["request"]["body"]["fields"] == {"resolution": {"name": "Fixed"}}

    fields = _structured(await live.call_tool("jira_get_edit_fields", {"key": "ABC-1"}))
    assert fields["issue_key"] == "ABC-1"
    types = _structured(await live.call_tool("jira_get_create_fields", {"project": "ABC"}))
    assert len(types["issue_types"]) == 3
    kinds = await live.call_tool("jira_list_link_types", {})
    assert "is blocked by" in str(_structured(kinds))
    with pytest.raises(ToolError, match="Nothing to change"):
        await live.call_tool("jira_edit_issue", {"key": "ABC-1"})
    assert len(fake.writes()) == 4
