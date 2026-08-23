"""Root conftest.

All Windows compatibility fixes live in ``tests/win_stubs.py``, which is
force-loaded by pytest *before* entry-point plugins via
``-p tests.win_stubs`` (see ``addopts`` in pyproject.toml). This file only
guarantees the stubs are present when pytest is invoked without that flag
(e.g. some IDE test runners), which works because by conftest-collection
time the entry-point plugins have already imported ``homeassistant.runner``
successfully or the stubs are still needed for later imports.
"""

from __future__ import annotations

import contextlib
import sys

if sys.platform == "win32":
    with contextlib.suppress(ImportError):
        from tests import win_stubs  # noqa: F401
