"""Jira Software's fields: Epic Name, Epic Link and Story Points."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AgentToolsError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import FieldChanges, JiraClient
from productivity_agent_tools.jira.fields import EPIC_LINK, EPIC_NAME, STORY_POINTS
from productivity_agent_tools.server import Connected, build_server
from tests.conftest import CREATE_FIELDS, EDITMETA, ISSUE, FakeJira

GH = "com.pyxis.greenhopper.jira"
FLOAT = "com.atlassian.jira.plugin.system.customfieldtypes:float"
EPIC_NAME_FIELD = {
    "fieldId": "customfield_10104",
    "name": "Epic Name",
    "required": True,
    "schema": {"type": "string", "custom": f"{GH}:gh-epic-label"},
}
EPIC_LINK_FIELD = {
    "fieldId": "customfield_10102",
    "name": "Epic Link",
    "schema": {"type": "any", "custom": f"{GH}:gh-epic-link"},
}
POINTS_FIELD = {
    "fieldId": "customfield_10106",
    "name": "Story Points",
    "schema": {"type": "number", "custom": FLOAT},
}
TYPES = "/jira/rest/api/2/issue/createmeta/ABC/issuetypes"


def _ok(body: Any) -> httpx.Response:
    return httpx.Response(200, json=body)


@pytest.fixture
def agile(fake: FakeJira) -> FakeJira:
    """A fake Jira with Jira Software's issue types and fields."""
    fake.overrides[TYPES] = _ok(
        {
            "values": [
                {"id": "8", "name": "Story", "subtask": False},
                {"id": "9", "name": "Epic", "subtask": False},
            ]
        }
    )
    common = CREATE_FIELDS["values"]
    fake.overrides[f"{TYPES}/9"] = _ok({"values": [*common, EPIC_NAME_FIELD]})
    fake.overrides[f"{TYPES}/8"] = _ok({"values": [*common, EPIC_LINK_FIELD, POINTS_FIELD]})
    fake.overrides["/jira/rest/api/2/field"] = _ok(
        [
            {"id": "summary", "name": "Summary", "schema": {"type": "string"}},
            {"id": "customfield_10104", "name": "Epic Name", "schema": EPIC_NAME_FIELD["schema"]},
            {"id": "customfield_10102", "name": "Epic Link", "schema": EPIC_LINK_FIELD["schema"]},
            {"id": "customfield_10106", "name": "Story Points", "schema": POINTS_FIELD["schema"]},
            {"id": "customfield_10999", "name": "Story Points", "schema": POINTS_FIELD["schema"]},
            {"name": "No id", "schema": POINTS_FIELD["schema"]},
        ]
    )
    return fake


def test_agile_fields_are_recognised_by_type_or_name() -> None:
    assert EPIC_NAME.matches("Anything", f"{GH}:gh-epic-label")
    assert EPIC_LINK.matches("Epic Link", f"{GH}:gh-epic-link")
    assert not EPIC_LINK.matches("Epic Link", None)
    assert STORY_POINTS.matches(" story points ", FLOAT)
    assert not STORY_POINTS.matches("Story Points", None)
    assert not STORY_POINTS.matches("Severity", FLOAT)


async def test_an_epic_is_created_with_its_epic_name(
    agile: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    with pytest.raises(AgentToolsError, match=r"also needs: epic_name \(Epic Name\)\. Ask the"):
        await jira.create_issue("ABC", "Epic", FieldChanges(summary="Uploads v2"))
    with pytest.raises(AgentToolsError, match="epic name must not be empty"):
        await jira.create_issue("ABC", "Epic", FieldChanges(summary="x", epic_name=" "))
    with pytest.raises(AgentToolsError, match="no Epic Link field on the create screen of Epic"):
        await jira.create_issue(
            "ABC", "Epic", FieldChanges(summary="x", epic_name="x", epic="ABC-9")
        )
    assert agile.writes() == []

    chosen, outcome = await jira.create_issue(
        "ABC", "epic", FieldChanges(summary="Uploads v2", epic_name=" Uploads ")
    )
    (sent,) = agile.writes()
    fields = json.loads(sent.content)["fields"]
    assert (chosen.name, outcome.sent) == ("Epic", True)
    assert (fields["issuetype"], fields["customfield_10104"]) == ({"id": "9"}, "Uploads")


async def test_a_story_joins_an_epic_and_takes_story_points(
    agile: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    with pytest.raises(AgentToolsError, match="not a valid issue key"):
        await jira.create_issue("ABC", "Story", FieldChanges(summary="x", epic="uploads"))
    await jira.create_issue(
        "ABC", "Story", FieldChanges(summary="Retry", epic="abc-7", story_points=3)
    )
    fields = json.loads(agile.writes()[-1].content)["fields"]
    assert (fields["customfield_10102"], fields["customfield_10106"]) == ("ABC-7", 3)


async def test_edit_sets_and_clears_the_epic_and_points(
    agile: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira(dry_run=False)
    with pytest.raises(AgentToolsError, match="no Story Points field on the edit screen"):
        await jira.edit_issue("ABC-1", FieldChanges(story_points=2))
    extra = {
        f["fieldId"]: {k: v for k, v in f.items() if k != "fieldId"}
        for f in (EPIC_LINK_FIELD, POINTS_FIELD)
    }
    agile.overrides["/jira/rest/api/2/issue/ABC-1/editmeta"] = _ok(
        {"fields": {**EDITMETA["fields"], **extra}}
    )
    changed, _ = await jira.edit_issue("ABC-1", FieldChanges(epic="ABC-7", story_points=0.5))
    assert changed == ["Epic Link", "Story Points"]
    assert json.loads(agile.writes()[-1].content)["fields"] == {
        "customfield_10102": "ABC-7",
        "customfield_10106": 0.5,
    }
    await jira.edit_issue("ABC-1", FieldChanges(epic="none", story_points=-1))
    assert json.loads(agile.writes()[-1].content)["fields"] == {
        "customfield_10102": None,
        "customfield_10106": None,
    }


async def test_reading_an_issue_shows_its_epic_and_points(
    agile: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    issue = {
        **ISSUE,
        "fields": {
            **ISSUE["fields"],
            "customfield_10102": "ABC-7",
            "customfield_10104": None,
            "customfield_10106": 5.0,
        },
    }
    agile.overrides["/jira/rest/api/2/issue/ABC-1"] = _ok(issue)
    jira = make_jira()
    detail = await jira.get_issue("ABC-1")
    assert (detail.epic, detail.epic_name, detail.story_points) == ("ABC-7", None, 5.0)
    asked = agile.requests[-1].url.params["fields"]
    assert asked.endswith(",customfield_10104,customfield_10102,customfield_10106")
    await jira.get_issue("ABC-1")
    assert sum(r.url.path.endswith("/field") for r in agile.requests) == 1


async def test_a_jira_without_agile_fields_reads_issues_as_before(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    detail = await make_jira().get_issue("ABC-1")
    assert (detail.epic, detail.epic_name, detail.story_points) == (None, None, None)
    assert "customfield" not in fake.requests[-1].url.params["fields"]


async def test_create_tool_passes_the_agile_fields(
    agile: FakeJira,
    make_settings: Callable[..., Settings],
    make_core: Callable[..., AtlassianClient],
) -> None:
    server = build_server(make_settings(), {"jira": Connected(make_core(), "test")})
    epic = {"project": "ABC", "issue_type": "Epic", "summary": "Uploads v2", "epic_name": "Up"}
    result = await server.call_tool("jira_create_issue", epic)
    body = getattr(result, "structured_content", {})["request"]["body"]
    assert body["fields"]["customfield_10104"] == "Up"
    story = {"key": "ABC-1", "epic": "ABC-7", "story_points": 2}
    agile.overrides["/jira/rest/api/2/issue/ABC-1/editmeta"] = _ok(
        {"fields": {**EDITMETA["fields"], "customfield_10102": EPIC_LINK_FIELD}}
    )
    with pytest.raises(Exception, match="no Story Points field"):
        await server.call_tool("jira_edit_issue", story)
