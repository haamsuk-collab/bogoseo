import re
from pathlib import Path

from bogoseo import keys
from bogoseo.collect.sources import adapters

REPO = Path(__file__).resolve().parent.parent


def test_catalog_lists_nine_api_sources():
    assert len(keys.api_sources()) == 9
    assert set(keys.api_sources()) == {a.name for a in adapters("api")}


def test_adapters_use_catalog_items():
    for a in adapters("api"):
        assert keys.source_item(a.name) == a.key_item


def test_readme_matches_catalog():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for item, spec in keys.catalog().items():
        assert item in readme and spec["issue_url"] in readme
    for s in keys.api_sources():
        assert s in readme


def test_keys_command_never_prints_values(monkeypatch):
    out = keys.render({"data_go_kr": "SECRETVALUE123"})
    assert "SECRETVALUE123" not in out and "입력됨" in out and "미입력" in out
