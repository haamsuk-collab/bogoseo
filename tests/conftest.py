import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
FIXTURES = REPO / "tests" / "fixtures"
# local real sample PDFs (gitignored) win; a fresh clone falls back to the synthetic fixtures
INBOX = (REPO / "inbox_sample") if (REPO / "inbox_sample").is_dir() else FIXTURES / "inbox"


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Isolated BOGOSEO_ROOT; the extract cache stays shared (repo .cache) so reruns are fast."""
    monkeypatch.setenv("BOGOSEO_ROOT", str(tmp_path))
    monkeypatch.delenv("BOGOSEO_SETTINGS", raising=False)
    return tmp_path
