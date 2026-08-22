"""Early-loading pytest plugin with Windows compatibility fixes.

This module is force-loaded by pytest via ``-p win_stubs`` (see ``addopts``
in pyproject.toml). The ``-p`` mechanism loads plugins *before* setuptools
entry-point plugins such as ``pytest-homeassistant-custom-component``, whose
plugin initialization imports ``homeassistant.runner``. A plain conftest.py
cannot handle this because conftests are collected after entry-point
plugins have already loaded.

Fixes applied on Windows:

1. POSIX-only modules: ``homeassistant.runner`` imports ``fcntl`` and
   ``homeassistant.util.resource`` imports ``resource``. Lightweight mocks
   are installed in ``sys.modules`` before those imports happen.

2. Event loop self-pipe vs pytest-socket: creating any asyncio event loop
   on Windows requires ``socket.socketpair()`` for its internal self-pipe.
   ``pytest-socket`` (pulled in by pytest-homeassistant-custom-component)
   replaces ``socket.socket`` with a guarding class that raises for every
   socket creation, which breaks event loop creation itself. We capture
   the real socket class *now* (before pytest-socket patches it) and wrap
   ``socket.socketpair`` so it temporarily restores the real class. Only
   asyncio's self-pipe uses ``socketpair`` in this suite; actual network
   access stays blocked.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

if sys.platform == "win32":
    for _module_name in ("fcntl", "resource"):
        if _module_name not in sys.modules:
            sys.modules[_module_name] = MagicMock()

    import socket as _socket_mod

    # Captured while socket.socket is still the genuine class.
    _REAL_SOCKET_CLS = _socket_mod.socket
    _ORIG_SOCKETPAIR = _socket_mod.socketpair

    def _unguarded_socketpair(
        *args: object, **kwargs: object
    ) -> tuple[_socket_mod.socket, _socket_mod.socket]:
        """socketpair that ignores pytest-socket's guarded socket class."""
        _cls_backup = _socket_mod.socket
        _socket_mod.socket = _REAL_SOCKET_CLS  # type: ignore[misc]
        try:
            return _ORIG_SOCKETPAIR(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            _socket_mod.socket = _cls_backup  # type: ignore[misc]

    _socket_mod.socketpair = _unguarded_socketpair
