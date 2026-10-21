import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
FIXTURES = REPO / "tests" / "fixtures"
INBOX = REPO / "inbox_sample"  # gitignored local sample PDFs (names: 표본_개선안_*.pdf)


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Isolated BOGOSEO_ROOT; the extract cache stays shared (repo .cache) so reruns are fast."""
    monkeypatch.setenv("BOGOSEO_ROOT", str(tmp_path))
    monkeypatch.delenv("BOGOSEO_SETTINGS", raising=False)
    return tmp_path
