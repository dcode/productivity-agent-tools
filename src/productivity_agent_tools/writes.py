"""Pieces shared by every product's write tools: the result shape and repeat handling.

Each product's write result extends ``WriteReport`` with what the change
applied to (a Jira issue, a Bitbucket pull request). ``report`` turns an
adapter's ``WriteOutcome`` into the shared fields, and ``once`` runs a change
through a ``RepeatGuard`` and marks a suppressed repeat.
"""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field, JsonValue

from productivity_agent_tools.api import WriteOutcome
from productivity_agent_tools.repeats import RepeatGuard


class WriteRequest(BaseModel):
    """The exact HTTP request a write tool sends, or would send."""

    method: str = Field(description="HTTP method.")
    path: str = Field(description="Request path on the product's host.")
    body: dict[str, JsonValue] = Field(description="JSON request body.")


class WriteReport(BaseModel):
    """Fields every write tool's result has."""

    action: str = Field(description="What the tool was asked to do.")
    sent: bool = Field(description="True if the change was sent.")
    dry_run: bool = Field(description="True if dry-run blocked the change.")
    request: WriteRequest = Field(description="The request that was, or would be, sent.")
    message: str = Field(description="Plain-language summary of the outcome.")
    repeat_suppressed: bool = Field(
        default=False,
        description="True if this call repeated an identical change made moments ago, so it "
        "was not sent a second time. The change exists once.",
    )


def report(action: str, product: str, outcome: WriteOutcome, done: str) -> dict[str, object]:
    """Build the shared fields of a write result from an adapter's outcome.

    Args:
        action: Short name of the action.
        product: Product name for the dry-run message, such as ``"Jira"``.
        outcome: Outcome from the API adapter.
        done: Sentence describing the change when it was sent.

    Returns:
        Keyword arguments for a ``WriteReport`` subclass.
    """
    message = (
        done
        if outcome.sent
        else (
            f"DRY RUN: nothing was sent to {product}. The request shown is what would be "
            "sent. The user must restart the server with --no-dry-run (or dry_run = false in "
            "the config) to allow writes."
        )
    )
    return {
        "action": action,
        "sent": outcome.sent,
        "dry_run": not outcome.sent,
        "request": WriteRequest(method=outcome.method, path=outcome.path, body=outcome.payload),
        "message": message,
    }


async def once[R: WriteReport](
    guard: RepeatGuard[R],
    key: tuple[str | int | bool | None, ...],
    send: Callable[[], Awaitable[R]],
) -> R:
    """Make a change unless the identical change was just made.

    Args:
        guard: Remembers recent changes.
        key: Identifies the change.
        send: Makes the change and builds its result.

    Returns:
        The result; for a repeat, the first result marked as not sent again.
    """
    result, age = await guard.run(key, send, lambda r: r.sent)
    if age is None or not result.sent:
        return result
    when = "is already in progress" if age < 1 else f"was made {round(age)} seconds ago"
    return result.model_copy(
        update={
            "repeat_suppressed": True,
            "message": f"{result.message} This identical request {when}, so it was not sent "
            "a second time; the change exists once.",
        }
    )
