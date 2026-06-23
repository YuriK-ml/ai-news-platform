from __future__ import annotations

import sys
from pathlib import Path


# Allow tests to import the package without requiring an editable install.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

