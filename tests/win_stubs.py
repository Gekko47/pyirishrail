"""Early-loading pytest plugin with Windows compatibility fixes.

This module is force-loaded by pytest via ``-p tests.win_stubs`` (see
``addopts`` in pyproject.toml). The ``-p`` mechanism loads plugins *before*
setuptools entry-point plugins such as
``pytest-homeassistant-custom-component``, whose plugin initialization
imports ``homeassistant.runner``. A plain conftest.py cannot handle this
because conftests are collected after entry-point plugins have already
loaded.

Fixes applied on Windows:

1. POSIX-only modules: ``homeassistant.runner`` imports ``fcntl`` and
   ``homeassistant.util.resource`` imports ``resource``. Lightweight mocks
   are installed in ``sys.modules`` before those imports happen.

2. Event loop self-pipe vs pytest-socket: creating any asyncio event loop
   on Windows requires ``socket.socketpair()`` for its internal self-pipe.
   ``pytest-socket`` (pulled in by pytest-homeassistant-custom-component)
   replaces ``socket.socket`` with a guarding class that raises for every
   socket creation, which breaks event loop creation itself. We capture
   the real socket class *now* (before pytest-socket patches it) and
   replace ``socket.socketpair`` with an implementation that builds the
   pair directly through that captured class, mirroring the stdlib's
   pure-Python Windows fallback (including its peer authentication).
   ``socket.socket`` itself is never swapped, so pytest-socket keeps
   enforcing its guard for socket creation in any other thread while a
   pair is under construction. Only asyncio's self-pipe uses
   ``socketpair`` in this suite; actual network access stays blocked.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

if sys.platform == "win32":
    for _module_name in ("fcntl", "resource"):
        if _module_name not in sys.modules:
            sys.modules[_module_name] = MagicMock()

    import contextlib
    import socket as _socket_mod

    # Captured while socket.socket is still the genuine class.
    _REAL_SOCKET_CLS = _socket_mod.socket
    # The original stdlib socketpair. Not called by the shim any more; kept
    # so regression tests can pause/poison it and prove that independence.
    _ORIG_SOCKETPAIR = _socket_mod.socketpair

    def _unguarded_socketpair(
        family: int = _socket_mod.AF_INET,
        type: int = _socket_mod.SOCK_STREAM,
        proto: int = 0,
    ) -> tuple[_socket_mod.socket, _socket_mod.socket]:
        """Return a connected socket pair built from the real class.

        Mirrors ``socket._fallback_socketpair`` (what Windows uses), but
        constructs every socket -- listener, client and the accepted peer
        -- through ``_REAL_SOCKET_CLS`` instead of the module-global
        ``socket.socket``, leaving pytest-socket's guard untouched.
        """
        if family == _socket_mod.AF_INET:
            host = "127.0.0.1"
        elif family == _socket_mod.AF_INET6:
            host = "::1"
        else:
            raise ValueError(
                "Only AF_INET and AF_INET6 socket address families are supported"
            )
        if type != _socket_mod.SOCK_STREAM:
            raise ValueError("Only SOCK_STREAM socket type is supported")
        if proto != 0:
            raise ValueError("Only protocol zero is supported")

        # Create a connected TCP socket. The trick with setblocking(False)
        # avoids having to spawn a thread, like the stdlib fallback.
        lsock = _REAL_SOCKET_CLS(family, type, proto)
        try:
            lsock.bind((host, 0))
            lsock.listen()
            # On IPv6, ignore flow_info and scope_id.
            addr, port = lsock.getsockname()[:2]
            csock = _REAL_SOCKET_CLS(family, type, proto)
            try:
                csock.setblocking(False)
                with contextlib.suppress(BlockingIOError, InterruptedError):
                    csock.connect((addr, port))
                csock.setblocking(True)
                # lsock.accept() would construct the peer via the guarded
                # global socket.socket, so replicate it manually.
                fd, _addr = lsock._accept()  # type: ignore[attr-defined]
                ssock = _REAL_SOCKET_CLS(family, type, proto, fileno=fd)
                ssock.setblocking(True)
            except BaseException:
                csock.close()
                raise
        finally:
            lsock.close()

        # Authenticate: avoid picking up a connection from something else
        # able to connect to {host}:{port} in the meantime.
        try:
            if (
                ssock.getsockname() != csock.getpeername()
                or csock.getsockname() != ssock.getpeername()
            ):
                raise ConnectionError("Unexpected peer connection")
        except OSError:
            ssock.close()
            csock.close()
            raise

        return (ssock, csock)

    _socket_mod.socketpair = _unguarded_socketpair
