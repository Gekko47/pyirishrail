import sys
from unittest.mock import MagicMock

# Mock fcntl for Windows
if sys.platform == "win32":
    mock_fcntl = MagicMock()
    sys.modules["fcntl"] = mock_fcntl
