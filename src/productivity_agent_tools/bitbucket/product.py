"""The Bitbucket product, as the server, configuration and CLI find it."""

from collections.abc import AsyncIterator

from productivity_agent_tools.bitbucket.client import BitbucketClient
from productivity_agent_tools.bitbucket.settings import BitbucketSettings, parse_bitbucket_settings
from productivity_agent_tools.bitbucket.tools import register_bitbucket_tools
from productivity_agent_tools.http import AtlassianClient
from productivity_agent_tools.products import RemoteProduct

INSTRUCTIONS: str = (
    "The bitbucket_ tools work with the user's self-hosted Bitbucket. Reads are safe to "
    "call freely. bitbucket_create_branch, bitbucket_add_pull_request_comment and "
    "bitbucket_review_pull_request change team-visible data: confirm the exact branch "
    "name, comment text or review with the user before calling them, and treat a DRY RUN "
    "result as nothing changed. To start work on a Jira issue, call bitbucket_create_branch "
    "with its issue_key and a branch_type. Most tools take a project key and a "
    "repository slug; when the user names the project they work in, call "
    "bitbucket_set_default_project once and leave project out afterwards. To say what "
    "still blocks a pull request, use bitbucket_get_pull_request_merge_status. Bitbucket "
    "text uses Markdown. "
    "When a bitbucket_ tool reports that the access token was rejected or cannot be found, "
    "do not retry: ask the user to store a new one with "
    "'productivity-agent-tools set-token bitbucket' in a terminal, then continue."
)


async def doctor(bitbucket: BitbucketClient) -> AsyncIterator[str]:
    """Check the server version and the pull-request listing.

    Args:
        bitbucket: The Bitbucket client.

    Yields:
        One line per check that passed.

    Raises:
        AgentToolsError: When a check fails.
    """
    yield f"Bitbucket version {await bitbucket.server_version()} at {bitbucket.base_url}"
    waiting = await bitbucket.list_pull_requests(max_results=25)
    more = "+" if waiting.more_available else ""
    yield f"pull requests readable ({waiting.returned}{more} open with you as reviewer)"


PRODUCT: RemoteProduct[BitbucketSettings, BitbucketClient] = RemoteProduct(
    name="bitbucket",
    title="Bitbucket",
    instructions=INSTRUCTIONS,
    settings_type=BitbucketSettings,
    adapter=AtlassianClient.from_connection,
    parse_settings=parse_bitbucket_settings,
    make_client=BitbucketClient,
    register_tools=register_bitbucket_tools,
    toolsets={
        "review": "Read a pull request's diff, changes, commits, activity, tasks and merge "
        "checks; comment and review.",
        "builds": "Build results, required builds and deployments.",
        "code": "Search code and create branches.",
    },
    doctor=doctor,
)
