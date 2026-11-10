import json
from collections.abc import Callable
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from productivity_agent_tools.config import Settings
from productivity_agent_tools.errors import AtlassianError
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.jira.client import JiraClient, normalize_issue_key
from productivity_agent_tools.server import build_server
from tests.conftest import FakeJira


@pytest.mark.parametrize("bad", ["", "ABC", "ABC-0", "abc-1/../x", "A B-1", "ABC-1?x=1"])
def test_issue_keys_are_validated(bad: str) -> None:
    with pytest.raises(AtlassianError):
        normalize_issue_key(bad)


def test_issue_key_is_normalized() -> None:
    assert normalize_issue_key(" abc-12 ") == "ABC-12"


async def test_search_returns_compact_rows(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    result = await make_jira().search("assignee = currentUser()", 500)
    assert (result.total, result.returned) == (7, 1)
    row = result.issues[0]
    assert (row.key, row.status, row.priority) == ("ABC-1", "To Do", "High")
    assert row.url == "https://host.example/jira/browse/ABC-1"
    assert fake.requests[0].url.params["maxResults"] == "100"


async def test_get_issue_keeps_only_newest_comments(make_jira: Callable[..., JiraClient]) -> None:
    issue = await make_jira().get_issue("abc-1", max_comments=2)
    assert issue.description == "h3. Details"
    assert issue.labels == ["ops"]
    assert [c.body for c in issue.comments] == ["note 2", "note 3"]
    assert issue.comment_total == 3


async def test_dry_run_comment_and_transition_send_no_writes(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    jira = make_jira()
    comment = await jira.add_comment("ABC-1", "hello")
    match, moved = await jira.transition_issue("ABC-1", "start progress")
    assert comment.sent is False and moved.sent is False
    assert match.id == "11"
    assert moved.payload == {"transition": {"id": "11"}}
    assert fake.writes() == []


async def test_live_transition_posts_resolved_id(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    match, outcome = await make_jira(dry_run=False).transition_issue("ABC-1", "Done")
    assert outcome.sent is True and match.to_status == "Done"
    assert json.loads(fake.writes()[0].content) == {"transition": {"id": "21"}}


async def test_unknown_transition_lists_what_is_available(
    fake: FakeJira, make_jira: Callable[..., JiraClient]
) -> None:
    with pytest.raises(AtlassianError, match="'Start Progress', 'Done'"):
        await make_jira(dry_run=False).transition_issue("ABC-1", "Reopen")
    assert fake.writes() == []


async def test_empty_comment_is_rejected(make_jira: Callable[..., JiraClient]) -> None:
    with pytest.raises(AtlassianError, match="empty"):
        await make_jira().add_comment("ABC-1", "   ")


def _structured(result: Any) -> dict[str, Any]:
    """Pull the structured payload out of a tool call result."""
    assert not result.is_error, result.content
    return dict(result.structured_content)


async def test_server_exposes_tools_with_read_write_annotations(
    make_core: Callable[..., AtlassianClient], make_settings: Callable[..., Settings]
) -> None:
    server = build_server(make_settings(), make_core(), "test")
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {
        "jira_status",
        "jira_list_presets",
        "jira_run_preset",
        "jira_search",
        "jira_get_issue",
        "jira_list_transitions",
        "jira_add_comment",
        "jira_transition_issue",
    }
    writes = {
        name
        for name, tool in tools.items()
        if tool.annotations is not None and not tool.annotations.read_only_hint
    }
    assert writes == {"jira_add_comment", "jira_transition_issue"}


async def test_tools_end_to_end_through_mcp(
    fake: FakeJira,
    make_core: Callable[..., AtlassianClient],
    make_settings: Callable[..., Settings],
) -> None:
    server = build_server(make_settings(), make_core(), "test source")

    status = _structured(await server.call_tool("jira_status", {}))
    assert status["display_name"] == "Pat Example"
    assert status["jira_version"] == "9.12.0"
    assert status["dry_run"] is True
    assert status["extra_cookies"] == ["consent"]

    preset = _structured(await server.call_tool("jira_run_preset", {"name": "mine-open"}))
    assert preset["issues"][0]["key"] == "ABC-1"

    comment = _structured(
        await server.call_tool("jira_add_comment", {"key": "ABC-1", "body": "hi"})
    )
    assert comment["sent"] is False
    assert comment["message"].startswith("DRY RUN")
    assert comment["request"]["body"] == {"body": "hi"}
    assert fake.writes() == []


async def test_tool_errors_reach_the_model_with_their_guidance(
    make_core: Callable[..., AtlassianClient], make_settings: Callable[..., Settings]
) -> None:
    server = build_server(make_settings(), make_core(token="expired"), "test")
    with pytest.raises(ToolError, match="rejected the access token") as info:
        await server.call_tool("jira_search", {"jql": "x"})
    assert not isinstance(info.value, UnexpectedToolError)
    with pytest.raises(ToolError, match=r"Available presets: .*mine-open"):
        await server.call_tool("jira_run_preset", {"name": "nope"})
