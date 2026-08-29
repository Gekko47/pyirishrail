"""Fixtures for the pure ``pyirishrail`` library test suite.

The library has no Home Assistant dependency, so this conftest only
re-enables sockets for tests that need ``aresponses`` — every other
restriction from the parent ``tests/win_stubs.py`` plugin (which is
loaded via ``addopts = "-p tests.win_stubs"``) remains in effect, so
the library suite can still catch accidental network access.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import pytest_socket


@pytest.fixture(autouse=True)
def _allow_aresponses_sockets(request: pytest.FixtureRequest) -> Iterator[None]:
    """Re-enable sockets for tests that use the aresponses fixture.

    The shared ``tests/win_stubs.py`` plugin blocks AF_INET socket
    creation globally to keep tests offline. ``aresponses`` starts a
    real mock HTTP server on a local port, so tests that need it must
    re-enable sockets for the duration of the test and restore blocking
    afterwards.
    """
    if "aresponses" in request.fixturenames:
        pytest_socket.enable_socket()
        yield
        pytest_socket.disable_socket(allow_unix_socket=True)
    else:
        yield
