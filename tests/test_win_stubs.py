"""Regression tests for the Windows ``socketpair`` shim in ``win_stubs``.

The shim must build asyncio self-pipe pairs through the captured real
``socket.socket`` class without ever swapping the process-wide
``socket.socket`` attribute, so pytest-socket keeps blocking socket
creation in every other thread at all times.
"""

from __future__ import annotations

import socket
import sys
import threading
from collections.abc import Callable

import pytest
import pytest_socket

from tests import win_stubs

# ── Smoke test (all platforms) ──────────────────────────────────────────────


def test_win_stubs_importable_on_all_platforms() -> None:
    """The shim module is importable on all platforms.

    The ``-p tests.win_stubs`` plugin mechanism force-loads this module
    on every developer's pytest invocation. This smoke test verifies the
    module imports cleanly on non-Windows hosts (where it no-ops) so CI
    on macOS/Linux does not fail at plugin load time.
    """
    # The import at module level already succeeded; assert the module
    # has the expected attributes regardless of platform.
    assert hasattr(win_stubs, "__doc__")
    if sys.platform == "win32":
        # Windows: the shim installs its socketpair wrapper.
        assert socket.socketpair is win_stubs._unguarded_socketpair
    else:
        # Non-Windows: the shim no-ops; socketpair is unchanged.
        assert socket.socketpair is not win_stubs._unguarded_socketpair


# ── Windows-only regression tests ──────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="win_stubs installs its fixes only on Windows",
)

_TIMEOUT = 10.0


def _assert_roundtrip(pair: tuple[socket.socket, socket.socket]) -> None:
    """Send one byte across a freshly created pair, then close it."""
    first, second = pair
    try:
        first.send(b"x")
        assert second.recv(1) == b"x"
    finally:
        first.close()
        second.close()


def _reset_blocked_socket_counter() -> None:
    """Drop PHACC's count of rejected socket attempts.

    pytest-homeassistant-custom-component replaces
    ``pytest_socket.SocketBlockedError`` with a variant that counts every
    rejected attempt, and its cleanup fixture fails any test during which
    an attempt was made ("the test opens sockets"). This module provokes
    rejected attempts *on purpose*, so reset the counter exactly like that
    cleanup fixture would.
    """
    instances = getattr(pytest_socket.SocketBlockedError, "instances", None)
    if instances is not None:
        instances.clear()


def test_module_installs_socketpair_wrapper() -> None:
    """The plugin must wire its wrapper in as ``socket.socketpair``."""
    assert socket.socketpair is win_stubs._unguarded_socketpair


def test_shim_does_not_call_orig_socketpair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pausing ``_ORIG_SOCKETPAIR`` must not affect pair construction."""

    def _paused(*args: object, **kwargs: object) -> tuple[socket.socket, socket.socket]:
        raise AssertionError("_unguarded_socketpair called _ORIG_SOCKETPAIR")

    monkeypatch.setattr(win_stubs, "_ORIG_SOCKETPAIR", _paused)
    _assert_roundtrip(win_stubs._unguarded_socketpair())


def test_second_thread_blocked_during_pair_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Other threads cannot create sockets while a pair is being built.

    Regression test: the wrapper used to restore the real ``socket.socket``
    class process-wide while the stdlib fallback ran, opening a window in
    which concurrent threads could create sockets past the pytest-socket
    guard (and two overlapping wrappers could even permanently un-guard the
    process). Both layers the shim may route through are paused
    mid-construction here; the guard must stay up regardless.
    """
    # Precondition: pytest-socket's guard is active for this test.
    assert socket.socket is not win_stubs._REAL_SOCKET_CLS

    real_cls = win_stubs._REAL_SOCKET_CLS
    orig_pair = win_stubs._ORIG_SOCKETPAIR
    entered = threading.Event()
    release = threading.Event()

    def _pause_then(call: Callable[..., object]) -> Callable[..., object]:
        """Wrap ``call`` so it blocks until the test releases it."""

        def gated(*args: object, **kwargs: object) -> object:
            entered.set()
            assert release.wait(_TIMEOUT), "deadlocked waiting for release"
            return call(*args, **kwargs)

        return gated

    # Pause whichever layer the shim ends up routing through.
    monkeypatch.setattr(win_stubs, "_REAL_SOCKET_CLS", _pause_then(real_cls))
    monkeypatch.setattr(win_stubs, "_ORIG_SOCKETPAIR", _pause_then(orig_pair))

    built: list[tuple[socket.socket, socket.socket] | BaseException] = []

    def build_pair() -> None:
        try:
            built.append(win_stubs._unguarded_socketpair())
        except Exception as err:
            built.append(err)

    builder = threading.Thread(target=build_pair, daemon=True, name="builder")
    builder.start()
    assert entered.wait(_TIMEOUT), "shim never reached pair construction"

    probed: list[socket.socket | Exception] = []

    def probe() -> None:
        try:
            probed.append(socket.socket())
        except Exception as err:
            probed.append(err)

    prober = threading.Thread(target=probe, daemon=True, name="prober")
    prober.start()
    prober.join(_TIMEOUT)
    assert not prober.is_alive(), "probe thread hung"
    # The probe below deliberately trips the guard; keep PHACC's cleanup
    # fixture from treating that as "the test opens sockets".
    _reset_blocked_socket_counter()

    (outcome,) = probed
    if isinstance(outcome, socket.socket):
        # Only reachable with the racy implementation; clean up before
        # failing so the leak does not linger.
        outcome.close()
    assert not isinstance(outcome, socket.socket), (
        "concurrent thread created a socket while a pair was under construction"
    )
    assert isinstance(outcome, pytest_socket.SocketBlockedError)

    release.set()
    builder.join(_TIMEOUT)
    assert not builder.is_alive(), "builder thread hung"

    (pair,) = built
    assert not isinstance(pair, BaseException)
    _assert_roundtrip(pair)
    # The guard must be back in place once construction finished, too.
    assert socket.socket is not win_stubs._REAL_SOCKET_CLS
