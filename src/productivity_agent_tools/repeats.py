"""Stop an agent from making the same change twice by accident.

Agents sometimes issue one write twice: a generated script that runs its body
two times, a retry after a slow answer, or two identical calls in one batch.
For a comment that means a visible duplicate; for a transition, two requests
racing each other on the server. ``RepeatGuard`` lets the first identical
request through and hands every repeat within a short window the first one's
result instead of sending again.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass

DEFAULT_WINDOW_SECONDS: float = 30.0


@dataclass(slots=True)
class _Entry[R]:
    """One remembered request.

    Attributes:
        result: Resolves to the first request's result.
        finished_at: Clock reading when it succeeded, or None while in flight.
    """

    result: asyncio.Future[R]
    finished_at: float | None = None


class RepeatGuard[R]:
    """Collapses identical requests made close together into one.

    Only requests that really changed something are remembered; a failed or
    blocked one can be tried again straight away.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the guard.

        Args:
            window_seconds: How long after a change an identical request counts as a repeat.
            clock: Monotonic clock, replaceable in tests.
        """
        self._window = window_seconds
        self._clock = clock
        self._entries: dict[Hashable, _Entry[R]] = {}

    async def run(
        self,
        key: Hashable,
        send: Callable[[], Awaitable[R]],
        changed: Callable[[R], bool],
    ) -> tuple[R, float | None]:
        """Send a request, or return the result of an identical recent one.

        Args:
            key: Identifies the request; equal keys mean the same change.
            send: Performs the request.
            changed: Tells whether a result means something was really changed.

        Returns:
            The result, and None if this call sent it, or otherwise how many
            seconds ago the identical request finished (0 if still in flight).

        Raises:
            Exception: Whatever ``send`` raises, for the first caller and for
                any repeat that was waiting on it.
        """
        now = self._clock()
        for stale in [
            k
            for k, e in self._entries.items()
            if e.finished_at is not None and now - e.finished_at >= self._window
        ]:
            del self._entries[stale]

        existing = self._entries.get(key)
        if existing is not None:
            age = 0.0 if existing.finished_at is None else now - existing.finished_at
            return await asyncio.shield(existing.result), age

        entry: _Entry[R] = _Entry(asyncio.get_running_loop().create_future())
        self._entries[key] = entry
        try:
            result = await send()
        except BaseException as exc:
            del self._entries[key]
            entry.result.set_exception(exc)
            entry.result.exception()  # mark as seen; waiting repeats still receive it
            raise
        entry.result.set_result(result)
        if changed(result):
            entry.finished_at = self._clock()
        else:
            del self._entries[key]
        return result, None
