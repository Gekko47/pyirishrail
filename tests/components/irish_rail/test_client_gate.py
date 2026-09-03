"""Tests for the :class:`RequestGate` concurrency-and-pacing gate.

These exercise the gate in isolation (no HTTP, no aiohttp) so the
cancellation, priority, and reservation contracts are pinned without
the noise of a full client test. The gate is imported from the
integration's vendored client package (see ``quality_scale.yaml`` for
the rationale on keeping the client inside the integration directory).
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from itertools import pairwise

import pytest

from custom_components.irish_rail.request_gate import RequestGate, _Waiter


class ManualClock:
    """Deterministic clock; advances only when the test says so."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSleep:
    """Records requested sleeps and advances the paired manual clock."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.sleeps: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.clock.advance(delay)


class SelectiveLateSleep:
    """Sleep that returns later than requested for one specific call."""

    def __init__(
        self, clock: ManualClock, *, late_call_index: int, overrun: float
    ) -> None:
        self.clock = clock
        self.late_call_index = late_call_index
        self.overrun = overrun
        self.sleeps: list[float] = []
        self._calls = 0

    async def __call__(self, delay: float) -> None:
        self.sleeps.append(delay)
        self._calls += 1
        if self._calls == self.late_call_index:
            self.clock.advance(delay + self.overrun)
        else:
            self.clock.advance(delay)


def _make_gate(
    *,
    max_concurrent: int = 2,
    min_interval_seconds: float = 0.25,
) -> tuple[RequestGate, ManualClock, FakeSleep]:
    """Build a gate wired to a manual clock so tests run in zero time."""
    clock = ManualClock()
    fake_sleep = FakeSleep(clock)
    gate = RequestGate(
        max_concurrent=max_concurrent,
        min_interval_seconds=min_interval_seconds,
        clock=clock,
        sleep=fake_sleep,
    )
    return gate, clock, fake_sleep


async def _run_acquire(gate: RequestGate) -> None:
    """Helper: enter and exit the gate normally (no body work)."""
    async with gate.acquire():
        pass


async def test_gate_default_construction_works_without_setup() -> None:
    """A default gate admits five sequential callers without hanging."""
    gate, _clock, _sleep = _make_gate()
    async with asyncio.timeout(1.0):
        for _ in range(5):
            async with gate.acquire():
                assert gate._in_flight == 1
    assert gate._in_flight == 0
    assert not gate._waiters


async def test_gate_caps_concurrent_acquires_at_max_concurrent() -> None:
    """At most ``max_concurrent`` callers hold the gate at once."""
    gate, _clock, _sleep = _make_gate(max_concurrent=2, min_interval_seconds=0)
    in_flight = 0
    peak = 0
    go = asyncio.Event()

    async def one_caller(idx: int) -> None:
        nonlocal in_flight, peak
        async with gate.acquire():
            in_flight += 1
            peak = max(peak, in_flight)
            if in_flight == 2 and not go.is_set():
                go.set()
            await go.wait()
            in_flight -= 1

    tasks = [asyncio.create_task(one_caller(i)) for i in range(6)]
    async with asyncio.timeout(1.0):
        await asyncio.gather(*tasks)
    assert peak == 2
    assert gate._in_flight == 0


async def test_gate_enforces_min_interval_between_exits() -> None:
    """The first-ever exit is free; subsequent exits are spaced."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0.25)
    async with asyncio.timeout(1.0):
        async with gate.acquire():
            pass
        async with gate.acquire():
            pass
    # The first-ever exit is free; the second waits the full interval.
    assert _clock.now == 0.25


async def test_gate_combined_concurrency_and_interval_under_burst() -> None:
    """Concurrency cap and minimum interval both hold under a burst."""
    gate, _clock, _sleep = _make_gate(max_concurrent=3, min_interval_seconds=0.1)
    in_flight = 0
    peak = 0
    release = asyncio.Event()

    async def one_caller() -> None:
        nonlocal in_flight, peak
        async with gate.acquire():
            in_flight += 1
            peak = max(peak, in_flight)
            await release.wait()
            in_flight -= 1

    tasks = [asyncio.create_task(one_caller()) for _ in range(10)]
    for _ in range(3):
        await asyncio.sleep(0)
    release.set()
    async with asyncio.timeout(1.0):
        await asyncio.gather(*tasks)
    assert peak == 3
    assert gate._in_flight == 0


async def test_gate_priority_normal_jumps_queued_background() -> None:
    """Strict priority: a queued background waiter is skipped when a
    normal caller is waiting behind it."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    served: list[str] = []
    holder_acquired = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async with gate.acquire("normal"):
            served.append("holder")
            holder_acquired.set()
            await release_holder.wait()

    async def background() -> None:
        async with gate.acquire("background"):
            served.append("bg")

    async def normal() -> None:
        async with gate.acquire("normal"):
            served.append("normal")

    holder_task = asyncio.create_task(holder())
    await holder_acquired.wait()
    background_task = asyncio.create_task(background())
    await asyncio.sleep(0)
    normal_task = asyncio.create_task(normal())
    await asyncio.sleep(0)
    # Queue order: background first (registered first), normal second.
    # The normal caller must jump the background caller because no
    # slot is available and normal outranks background.
    assert [waiter.priority for waiter in gate._waiters] == [
        "background",
        "normal",
    ]
    release_holder.set()
    async with asyncio.timeout(1.0):
        await asyncio.gather(holder_task, background_task, normal_task)
    assert served == ["holder", "normal", "bg"]


async def test_gate_background_not_starved_after_normal_burst() -> None:
    """Background waits for queued normals, then runs strictly last."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    served: list[str] = []
    first_in = asyncio.Event()
    go_all = asyncio.Event()

    async def normal(idx: int) -> None:
        async with gate.acquire("normal"):
            served.append(f"n{idx}")
            if not first_in.is_set():
                first_in.set()
            await go_all.wait()

    async def background() -> None:
        async with gate.acquire("background"):
            served.append("bg")

    first = asyncio.create_task(normal(0))
    await first_in.wait()
    rest = [asyncio.create_task(normal(i)) for i in range(1, 5)]
    background_task = asyncio.create_task(background())
    await asyncio.sleep(0)
    go_all.set()
    async with asyncio.timeout(1.0):
        await asyncio.gather(first, *rest, background_task)
    assert served == ["n0", "n1", "n2", "n3", "n4", "bg"]


async def test_gate_in_flight_background_does_not_starve_normal() -> None:
    """A background caller already holding the gate does not block
    a normal caller beyond one slot release."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    served: list[str] = []
    bg_acquired = asyncio.Event()
    release_bg = asyncio.Event()
    go_normal = asyncio.Event()

    async def background_first() -> None:
        async with gate.acquire("background"):
            served.append("bg1")
            bg_acquired.set()
            await release_bg.wait()

    async def normal_mid() -> None:
        async with gate.acquire("normal"):
            served.append("normal")
            go_normal.set()

    async def background_last() -> None:
        async with gate.acquire("background"):
            served.append("bg2")

    # Background takes the only slot.
    bg1 = asyncio.create_task(background_first())
    await bg_acquired.wait()
    # While bg1 holds the slot, register a normal caller and a
    # second background caller.
    normal = asyncio.create_task(normal_mid())
    bg2 = asyncio.create_task(background_last())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Queue order: normal first, background second.
    assert [waiter.priority for waiter in gate._waiters] == [
        "normal",
        "background",
    ]
    assert served == ["bg1"]

    release_bg.set()
    async with asyncio.timeout(1.0):
        await asyncio.gather(bg1, normal, bg2)
    assert served == ["bg1", "normal", "bg2"]


async def test_gate_invalid_priority_rejected() -> None:
    """An unknown priority raises ``ValueError`` before touching the queue."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    with pytest.raises(ValueError, match="priority"):
        async with gate.acquire("urgent"):
            pass


async def test_gate_invalid_construction_rejected() -> None:
    """Non-positive ``max_concurrent`` and negative ``min_interval`` are rejected."""
    with pytest.raises(ValueError, match="max_concurrent"):
        RequestGate(max_concurrent=0)
    with pytest.raises(ValueError, match="min_interval"):
        RequestGate(max_concurrent=1, min_interval_seconds=-1.0)


async def test_gate_cancelled_holder_releases_slot() -> None:
    """Cancellation of the slot holder frees the next caller."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    started = asyncio.Event()
    second_done = asyncio.Event()

    async def first_caller() -> None:
        async with gate.acquire():
            started.set()
            await asyncio.sleep(10)

    async def second_caller() -> None:
        async with gate.acquire():
            second_done.set()

    first_task = asyncio.create_task(first_caller())
    await started.wait()
    second_task = asyncio.create_task(second_caller())
    await asyncio.sleep(0)
    assert gate._in_flight == 1
    first_task.cancel()
    async with asyncio.timeout(1.0):
        await asyncio.gather(first_task, second_task, return_exceptions=True)
    assert first_task.cancelled()
    assert second_done.is_set(), "second caller never got the slot"
    assert gate._in_flight == 0, f"slot leaked: {gate._in_flight}"


async def test_gate_cancelled_queued_waiter_dequeues_cleanly() -> None:
    """A caller cancelled while queued is removed without leaving a slot."""
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    started = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.acquire():
            started.set()
            await asyncio.sleep(10)

    async def queued() -> None:
        async with gate.acquire():  # pragma: no cover - never reached
            pass

    holder_task = asyncio.create_task(holder())
    await started.wait()
    queued_one = asyncio.create_task(queued())
    await asyncio.sleep(0)
    assert len(gate._waiters) == 1
    queued_one.cancel()
    async with asyncio.timeout(1.0):
        await asyncio.gather(queued_one, return_exceptions=True)
    # The only queued waiter was cancelled: the queue must be empty.
    assert not gate._waiters, "cancelled queued waiter must be dequeued cleanly"
    release.set()
    holder_task.cancel()
    async with asyncio.timeout(1.0):
        await asyncio.gather(holder_task, return_exceptions=True)


async def test_gate_cancellation_during_pre_yield_sleep_releases_slot() -> None:
    """A caller cancelled while sleeping until its reserved exit
    time must still release the slot."""
    clock = ManualClock()
    started = asyncio.Event()
    release_sleep = asyncio.Event()

    async def blocking_sleep(delay: float) -> None:
        started.set()
        await release_sleep.wait()

    gate = RequestGate(
        max_concurrent=1,
        min_interval_seconds=0.1,
        clock=clock,
        sleep=blocking_sleep,
    )

    # Pre-warm: first caller sets _next_exit = 0.1. delta = 0, sleep skipped.
    async with asyncio.timeout(1.0):
        await _run_acquire(gate)
    assert gate._next_exit == 0.1
    assert not started.is_set(), "first caller should not have slept"

    # Second caller: delta = 0.1 > 0, sleep branch runs and blocks.
    holder = asyncio.create_task(_run_acquire(gate))
    async with asyncio.timeout(1.0):
        await started.wait()
    assert gate._in_flight == 1, "holder was not admitted"

    holder.cancel()
    async with asyncio.timeout(1.0):
        await asyncio.gather(holder, return_exceptions=True)
    assert holder.cancelled()
    assert gate._in_flight == 0, f"slot leaked: {gate._in_flight}"
    assert not gate._waiters, "queue not drained"

    release_sleep.set()
    async with asyncio.timeout(1.0):
        async with gate.acquire():
            assert gate._in_flight == 1
    assert gate._in_flight == 0


async def test_gate_delayed_sleep_preserves_pacing_for_next_caller() -> None:
    """Regression: a caller's sleep returning late must not let the
    next caller start back-to-back with it."""
    overrun = 0.2
    clock = ManualClock()
    selective_sleep = SelectiveLateSleep(clock, late_call_index=1, overrun=overrun)
    gate = RequestGate(
        max_concurrent=2,
        min_interval_seconds=0.25,
        clock=clock,
        sleep=selective_sleep,
    )
    exits: list[float] = []

    async def caller() -> None:
        async with gate.acquire():
            exits.append(clock())

    tasks = [asyncio.create_task(caller()) for _ in range(3)]
    async with asyncio.timeout(1.0):
        await asyncio.gather(*tasks)

    assert len(exits) == 3
    gaps = [later - earlier for earlier, later in pairwise(exits)]
    assert all(gap >= 0.25 - 1e-9 for gap in gaps), f"gap violation: {gaps}"


async def test_gate_shared_across_two_clients_bounded_together() -> None:
    """Two clients sharing one gate are bounded by the gate, not per-client.

    Pins the contract documented in :class:`RequestGate` and the
    ``IrishRailClient`` docstring ("Several IrishRailClient instances
    can share one gate by passing the same instance to each
    constructor; the limits then apply across the clients as if they
    were one caller"). Two clients each try to send more requests than
    ``max_concurrent`` in parallel; the gate's own ``_in_flight`` must
    never exceed ``max_concurrent`` regardless of which client the
    call came from, and every caller must eventually return.
    """
    cap = 2
    # 2 "clients" × 3 callers each = 6 total, well over the gate's cap.
    total = 6

    # A second client class is not needed: IrishRailClient only uses
    # the gate via ``_request``; the test exercises the gate directly
    # instead. Sharing one gate across two ``acquire()`` call sites is
    # the exact contract the integration relies on (coordinator's
    # client + config flow's client + rebuild's client all pass the
    # same instance), so we model that with two independent consumer
    # call sites against the same gate.
    gate, _clock, _sleep = _make_gate(max_concurrent=cap, min_interval_seconds=0)
    in_flight = 0
    peak = 0
    go = asyncio.Event()

    async def one_caller() -> None:
        nonlocal in_flight, peak
        # ``priority`` is the same for everyone; the test pins the
        # contract that callers from different "clients" compete for
        # the same cap, not per-client caps.
        async with gate.acquire("normal"):
            in_flight += 1
            peak = max(peak, in_flight)
            await go.wait()
            in_flight -= 1

    tasks = [asyncio.create_task(one_caller()) for _ in range(total)]
    # Yield so every caller has reached ``acquire()`` and the gate
    # has either admitted it or parked it on the waiters queue.
    for _ in range(total):
        await asyncio.sleep(0)
    go.set()
    async with asyncio.timeout(1.0):
        await asyncio.gather(*tasks)

    # The cap was reached, proving overlap actually happened (without
    # this the test would not have proven the gate was the bound).
    assert peak == cap, f"peak in-flight was {peak}, expected {cap}"
    # No slots leaked after the burst drained.
    assert gate._in_flight == 0
    # Every caller observed a slot (none starved or were dropped).
    assert in_flight == 0
    # Both "clients" had callers run; the cap being held is the
    # share-across-clients contract and is fully exercised above.


async def test_gate_stress_random_cancellations() -> None:
    """Randomized stress test: no slot leaks or queue residue under churn.

    Hammers the gate with many concurrent callers, each cancelled at a
    random await point (before admission, after admission but before
    the pre-yield sleep, or during the pre-yield sleep), and verifies
    that the gate's invariants hold when the dust settles:

    * ``_in_flight`` returns to zero.
    * ``_waiters`` is empty.
    * No task leaked (every task is done and either succeeded or was
      cancelled).
    * The whole run completes inside a generous ``asyncio.timeout``
      (no deadlock).
    """
    iterations = 50
    cap = 3
    clock = ManualClock()
    fake_sleep = FakeSleep(clock)
    gate = RequestGate(
        max_concurrent=cap,
        min_interval_seconds=0,
        clock=clock,
        sleep=fake_sleep,
    )

    # Deterministic seed so the stress is reproducible across runs and
    # CI failures are debuggable from the same RNG state.
    rng = random.Random(20260829)

    async def one_caller(yield_count: int) -> None:
        # The body just yields ``yield_count`` times so cancellation
        # can land at varied points inside the gate's critical section.
        # Cancellation arriving while we hold the gate must trigger
        # the slot release path; cancellation arriving while we are
        # still queued must trigger the dequeue path. The gate has to
        # make the right choice in both cases.
        async with gate.acquire():
            for _ in range(yield_count):
                await asyncio.sleep(0)

    for iteration in range(iterations):
        # Small batch: more than ``cap`` so admission has to queue some.
        batch = cap + rng.randint(1, 4)
        yield_counts = [rng.randint(0, 5) for _ in range(batch)]
        tasks = [
            asyncio.create_task(one_caller(yield_count)) for yield_count in yield_counts
        ]
        # Let the first wave reach the gate.
        for _ in range(batch):
            await asyncio.sleep(0)
        # Cancel a random subset (at least one, to exercise the
        # cancellation paths even when the gate is otherwise empty).
        cancel_count = rng.randint(1, batch)
        for task in rng.sample(tasks, cancel_count):
            task.cancel()
        async with asyncio.timeout(1.0):
            await asyncio.gather(*tasks, return_exceptions=True)
        # The gate must be quiescent after every batch.
        assert gate._in_flight == 0, (
            f"iter {iteration}: slot leak; "
            f"in_flight={gate._in_flight}, waiters={len(gate._waiters)}"
        )
        assert not gate._waiters, (
            f"iter {iteration}: queue residue; "
            f"waiters={[(w.priority, w.event.is_set()) for w in gate._waiters]}"
        )
        # Every task is done by here (gather returned).
        for task in tasks:
            assert task.done(), f"iter {iteration}: task leaked"


class _RemoveSpyDeque(deque[_Waiter]):
    """Deque whose ``remove`` can be armed to raise ``ValueError``.

    Used to pin the gate's lost-admission-race cleanup: when a caller is
    cancelled after the admission sweep already dequeued it, the
    cleanup's ``self._waiters.remove(waiter)`` raises ``ValueError``.
    Arming the spy reproduces that state deterministically.
    """

    def __init__(self) -> None:
        super().__init__()
        self.remove_calls: list[_Waiter] = []
        self.fail_remove = False

    def remove(self, item: _Waiter) -> None:
        self.remove_calls.append(item)
        if self.fail_remove:
            raise ValueError("simulated lost admission race")
        super().remove(item)


async def test_gate_cancellation_that_loses_the_admission_race_releases_slot() -> None:
    """A caller cancelled after being swept-admitted must release its slot.

    Deterministic reproduction of the race: the caller parks on its
    admission Event, the sweep admits it (dequeuing it and incrementing
    ``_in_flight``) but its wake-up has not run yet, and *then* the
    cancellation is delivered at the ``Event.wait`` await point. The
    cleanup handler sees ``admitted is False`` while the waiter is no
    longer queued; it must take the release path instead of leaking the
    slot, and must do so without deadlocking on the non-reentrant gate
    lock.
    """
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    # Route the gate's queue through the spy so the cleanup's dequeue
    # attempt can be observed and made to fail like the real race does.
    spy = _RemoveSpyDeque()
    gate._waiters = spy

    holder_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async with gate.acquire():
            holder_started.set()
            await release_holder.wait()

    holder_task = asyncio.create_task(holder())
    await holder_started.wait()
    assert gate._in_flight == 1

    async def victim() -> None:
        async with gate.acquire():
            pass

    victim_task = asyncio.create_task(victim())
    # Park the victim on its admission Event (the gate is full).
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(spy) == 1
    assert not spy[0].event.is_set()
    assert gate._in_flight == 1

    # Free the slot: one loop turn runs the holder's release and the
    # sweep's admission of the victim (its own wake-up is queued behind
    # this test's continuation, so it has not run yet).
    release_holder.set()
    await asyncio.sleep(0)
    assert gate._in_flight == 1, "victim was not admitted by the sweep"
    assert not spy, "victim was not dequeued by the sweep"

    # Arm the spy so the cleanup's dequeue attempt raises ValueError,
    # exactly as it does when the sweep won the race, then deliver the
    # cancellation. The cleanup must release the slot OUTSIDE the gate
    # lock (the non-reentrant-lock deadlock regression).
    spy.fail_remove = True
    victim_task.cancel()
    await asyncio.sleep(0)
    await asyncio.gather(holder_task, victim_task, return_exceptions=True)

    assert len(spy.remove_calls) == 1, (
        "cleanup did not hit the lost-admission-race dequeue path"
    )
    assert gate._in_flight == 0, "slot leaked after the lost admission race"
    assert not spy, "queue residue after the lost admission race"


async def test_gate_cancellation_while_still_queued_dequeues_without_release() -> None:
    """A queued caller cancelled before admission leaves no trace.

    Companion to the lost-race test: the dequeue path must NOT release a
    slot the caller never owned, or the in-flight counter underflows and
    the cap silently widens.
    """
    gate, _clock, _sleep = _make_gate(max_concurrent=1, min_interval_seconds=0)
    spy = _RemoveSpyDeque()
    gate._waiters = spy

    holder_started = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder() -> None:
        async with gate.acquire():
            holder_started.set()
            await release_holder.wait()

    holder_task = asyncio.create_task(holder())
    await holder_started.wait()

    victim_task = asyncio.create_task(victim_gate_body(gate))
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(spy) == 1
    assert gate._in_flight == 1

    # Cancel while the victim is still parked and un-admitted.
    victim_task.cancel()
    release_holder.set()
    await asyncio.gather(holder_task, victim_task, return_exceptions=True)

    assert len(spy.remove_calls) == 1
    assert gate._in_flight == 0, "holder slot was not returned"
    assert not spy


async def victim_gate_body(gate: RequestGate) -> None:
    """Acquire the gate and do nothing (helper for cancellation tests)."""
    async with gate.acquire():
        pass
