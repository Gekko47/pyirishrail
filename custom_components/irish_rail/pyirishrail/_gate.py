"""Concurrency-and-pacing gate for outbound Irish Rail API requests.

The public api.irishrail.ie endpoints are shared infrastructure: one
Home Assistant instance polling several stations, plus occasional
configuration-flow lookups, all draw from the same unauthenticated rate
budget. :class:`RequestGate` is the single point through which every
outbound request of a client passes, enforcing two coupled limits:

* ``max_concurrent`` — at most N requests in flight at any instant.
* ``min_interval_seconds`` — minimum wall-clock spacing between two
  consecutive *gate exits* (the moment an admitted request actually
  starts its HTTP call).

Design notes (all properties are enforced structurally and pinned by
``tests/components/irish_rail/test_client_gate.py``):

* **One admission function.**
  :meth:`RequestGate._admit_eligible_waiters_locked` is the only code
  that decides who is admitted. It runs at exactly two sites: after a
  caller registers itself in ``acquire()`` and after a caller gives its
  slot back in :meth:`RequestGate._release_slot`. A waiter is
  registered in the queue *before* any admission decision is made about
  it, which removes the "registered vs. admitted" ambiguity that
  produced deadlocks/livelocks in earlier designs.
* **One Event, waited on exactly once.** ``acquire()`` registers, calls
  the admission function, then awaits its waiter's event a single time.
  If the sweep admitted the caller, the event is already set and the
  wait returns immediately; otherwise a future release's sweep sets it.
  No retry loops, no second waits.
* **Exit-time reservations (two-phase).** Each admission reserves the
  caller's earliest allowed gate-exit time under the lock
  (``_next_exit``), so callers admitted in the same sweep exit spaced
  ``min_interval_seconds`` apart instead of all leaving simultaneously
  after a shared sleep. Immediately before each request begins, the
  caller re-acquires the lock and pushes ``_next_exit`` to at least
  ``now + min_interval``, so a caller whose sleep returned late (or
  whose path from the wait to yield was slow) still leaves the gate
  properly spaced from the next caller. ``_next_exit`` is written from
  exactly two places: ``_admit_eligible_waiters_locked`` (per
  admission) and ``acquire`` (per actual exit). ``None`` means "no
  admission ever" so the first caller never waits.
* **Cancellation safety.** A cancelled caller either gives back the
  slot it owns (releasing it for the next eligible waiter) or removes
  itself from the queue before it was admitted. The event being set is
  exactly the marker that the sweep incremented the in-flight count on
  the caller's behalf, so the cleanup branch can be chosen without
  races. A randomized stress test hammers these paths.
* **Strict priority.** ``priority="background"`` callers are only
  admitted when no ``"normal"`` caller is queued, so bulk work (e.g. a
  network-wide sampling run) cannot delay live polling sharing the
  same gate. When nothing normal is queued, background callers are
  served in FIFO order and are not starved. Priority governs
  *admission order*, not preemption: a background caller that has
  already crossed the gate is allowed to finish its current HTTP
  call, so a live poll that arrives while the gate is full of
  background traffic is admitted within one slot release, not
  blocked until the background sweep ends.

Throughput derivation: the steady-state admission rate is bounded by
``1 / min_interval_seconds`` (4 req/s with the 0.25 s default),
*assuming each request completes in less than ``min_interval_seconds``*
— if requests take longer, the next admission happens only after the
previous release and throughput drops accordingly. ``max_concurrent``
bounds burst parallelism; it is not a throughput knob.
"""

from __future__ import annotations

import asyncio
import collections
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

__all__ = ["RequestGate"]


class _Waiter:
    """A queued (or admitted) caller and its per-admission state."""

    __slots__ = ("event", "exit_at", "priority")

    def __init__(self, priority: str) -> None:
        self.priority = priority
        self.event = asyncio.Event()
        # Reserved gate-exit time, written by the admission sweep before
        # the event is set. Only read after the wait succeeds.
        self.exit_at: float = 0.0


class RequestGate:
    """Coordinated throttle for outbound Irish Rail API requests.

    Usage::

        async with gate.acquire():
            await session.get(...)

    Several :class:`pyirishrail.api.IrishRailClient` instances can share
    one gate by passing the same instance to each constructor; the
    limits then apply across the clients as if they were one caller.

    Args:
        max_concurrent: Maximum number of requests in flight at once.
        min_interval_seconds: Minimum spacing between consecutive gate
            exits, in seconds.
        clock: Monotonic clock supplier (injectable for tests).
        sleep: Sleep awaitable (injectable for tests).
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        min_interval_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self._max_concurrent = max_concurrent
        self._min_interval = float(min_interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._waiters: collections.deque[_Waiter] = collections.deque()
        self._in_flight = 0
        # Reserved gate-exit time of the most recent admission. Written
        # ONLY by _admit_eligible_waiters_locked (under the lock). None
        # means no admission has ever been reserved.
        self._next_exit: float | None = None

    def _admit_eligible_waiters_locked(self) -> None:
        """Admit queued waiters while slots and pacing allow.

        Must be called with ``self._lock`` held. The single source of
        truth for admission: callers register first, then call this; a
        release calls it again. Each admission reserves the waiter's
        exit time here, so two callers admitted in one sweep cannot
        leave the gate simultaneously.
        """
        while self._in_flight < self._max_concurrent and self._waiters:
            # First normal-priority waiter wins; a background waiter is
            # served only when no normal caller is queued.
            index = 0
            for i, waiter in enumerate(self._waiters):
                if waiter.priority == "normal":
                    index = i
                    break
            candidate = self._waiters[index]
            del self._waiters[index]
            now = self._clock()
            if self._next_exit is None:
                exit_at = now
            else:
                exit_at = max(self._next_exit, now)
            self._next_exit = exit_at + self._min_interval
            self._in_flight += 1
            candidate.exit_at = exit_at
            candidate.event.set()

    async def _release_slot(self) -> None:
        """Give back one in-flight slot and admit whoever is eligible."""
        async with self._lock:
            self._in_flight -= 1
            self._admit_eligible_waiters_locked()

    @asynccontextmanager
    async def acquire(self, priority: str = "normal") -> AsyncIterator[None]:
        """Wait for a slot and the reserved interval, then yield.

        Args:
            priority: ``"normal"`` or ``"background"``. Background
                callers yield to any queued normal caller.

        Raises:
            ValueError: If ``priority`` is not a known class.
        """
        if priority not in ("normal", "background"):
            raise ValueError(
                f"priority must be 'normal' or 'background', got {priority!r}"
            )
        waiter = _Waiter(priority)
        slot_owned = False
        try:
            async with self._lock:
                self._waiters.append(waiter)
                self._admit_eligible_waiters_locked()
            # Exactly one wait on exactly one Event. If the sweep above
            # admitted us the event is already set and this returns
            # immediately; otherwise a future release's sweep sets it.
            await waiter.event.wait()
            # No await point between the wait returning and the flag,
            # so cancellation cannot split them.
            slot_owned = True
            # Sleep (outside the lock) until OUR reserved exit time.
            delta = waiter.exit_at - self._clock()
            if delta > 0:
                await self._sleep(delta)
            # Re-reserve the next pacing interval at the ACTUAL exit
            # time (under the lock) so a caller whose sleep returned
            # late still leaves the gate properly spaced from the next
            # caller. Without this, the next caller's reservation
            # (computed at their admission from the now-stale
            # _next_exit) could place them too close to the actual
            # exit. The floor is `now + min_interval`; any existing
            # reservation further in the future is preserved.
            async with self._lock:
                now = self._clock()
                floor = now + self._min_interval
                if self._next_exit is None or floor > self._next_exit:
                    self._next_exit = floor
        except BaseException:
            if slot_owned or waiter.event.is_set():
                # We own a slot: either we got past the wait, or
                # cancellation was delivered at the wait() await point
                # after the sweep had already admitted us (the event
                # being set is exactly the marker that _in_flight was
                # incremented on our behalf).
                await self._release_slot()
            else:
                # Still queued (or never admitted): take ourselves out.
                # The removal cannot raise ValueError: the gate lock is
                # never held across an await, so no admission sweep can
                # run between the event check above and this removal. If
                # a refactor ever breaks that invariant this line fails
                # loudly in tests instead of silently leaking an admitted
                # slot.
                async with self._lock:
                    self._waiters.remove(waiter)
            raise
        try:
            yield
        finally:
            await self._release_slot()
