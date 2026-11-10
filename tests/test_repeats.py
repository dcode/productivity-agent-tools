import asyncio

from productivity_agent_tools.repeats import RepeatGuard


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Sender:
    def __init__(self, result: str = "done") -> None:
        self.calls = 0
        self.result = result
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self) -> str:
        self.calls += 1
        await self.release.wait()
        return self.result


def changed(result: str) -> bool:
    return result == "done"


async def test_repeat_inside_the_window_reuses_the_first_result() -> None:
    clock, send = Clock(), Sender()
    guard: RepeatGuard[str] = RepeatGuard(window_seconds=30, clock=clock)
    assert await guard.run("k", send, changed) == ("done", None)
    clock.now += 12
    assert await guard.run("k", send, changed) == ("done", 12.0)
    assert await guard.run("other", send, changed) == ("done", None)
    assert send.calls == 2


async def test_request_is_sent_again_once_the_window_has_passed() -> None:
    clock, send = Clock(), Sender()
    guard: RepeatGuard[str] = RepeatGuard(window_seconds=30, clock=clock)
    await guard.run("k", send, changed)
    clock.now += 30
    assert await guard.run("k", send, changed) == ("done", None)
    assert send.calls == 2


async def test_simultaneous_identical_requests_send_once() -> None:
    send = Sender()
    send.release.clear()
    guard: RepeatGuard[str] = RepeatGuard()
    first = asyncio.create_task(guard.run("k", send, changed))
    second = asyncio.create_task(guard.run("k", send, changed))
    await asyncio.sleep(0)
    send.release.set()
    assert sorted(await asyncio.gather(first, second), key=str) == [("done", 0.0), ("done", None)]
    assert send.calls == 1


async def test_unchanged_results_are_not_remembered() -> None:
    send = Sender(result="blocked")
    guard: RepeatGuard[str] = RepeatGuard()
    assert await guard.run("k", send, changed) == ("blocked", None)
    assert await guard.run("k", send, changed) == ("blocked", None)
    assert send.calls == 2


async def test_failure_reaches_waiting_repeats_and_can_be_retried() -> None:
    gate = asyncio.Event()
    attempts = 0

    async def send() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await gate.wait()
            raise RuntimeError("boom")
        return "done"

    guard: RepeatGuard[str] = RepeatGuard()
    first = asyncio.create_task(guard.run("k", send, changed))
    second = asyncio.create_task(guard.run("k", send, changed))
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert [type(r) for r in results] == [RuntimeError, RuntimeError]
    assert attempts == 1
    assert await guard.run("k", send, changed) == ("done", None)
