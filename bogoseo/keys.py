"""Key catalogue (keys_catalog.yaml is the single source) and key status; never exposes key values."""
from __future__ import annotations

from pathlib import Path

import yaml

from . import config


def catalog_path() -> Path:
    return Path(__file__).with_name("keys_catalog.yaml")


def catalog() -> dict:
    """{item: {issue_url, note, sources:[...]}} in file order."""
    with open(catalog_path(), encoding="utf-8") as f:
        return yaml.safe_load(f)["items"]


def api_sources() -> list:
    return [s for spec in catalog().values() for s in spec["sources"]]


def source_item(source: str):
    for item, spec in catalog().items():
        if source in spec["sources"]:
            return item
    return None


def status(keys: dict | None = None) -> list:
    """[(item, present: bool, issue_url, sources)] — presence only."""
    keys = config.load_keys() if keys is None else keys
    return [(item, bool(keys.get(item)), spec["issue_url"], spec["sources"]) for item, spec in catalog().items()]


def render(keys: dict | None = None) -> str:
    lines = [f"키 파일: {config.keys_path()}"]
    for item, present, url, sources in status(keys):
        lines.append(f"{item}: {'입력됨' if present else '미입력'}")
        lines.append(f"  발급처: {url}")
        lines.append(f"  사용 소스: {', '.join(sources)}")
    return "\n".join(lines)
