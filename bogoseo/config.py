"""Paths and settings. Tests point BOGOSEO_ROOT at a tmp dir."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent


def repo_dir() -> Path:
    return _REPO


def root() -> Path:
    env = os.environ.get("BOGOSEO_ROOT")
    return Path(env) if env else _REPO


def cache_dir() -> Path:
    env = os.environ.get("BOGOSEO_CACHE")
    return Path(env) if env else _REPO / ".cache"


def refs_dir() -> Path:
    return root() / "references"


def reports_dir() -> Path:
    return root() / "reports"


def inbox_dir() -> Path:
    return root() / "inbox"


def state_dir() -> Path:
    return root() / "state"


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(p: Path) -> dict:
    try:
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def settings() -> dict:
    merged: dict = {}
    paths = [_REPO / "settings.yaml", Path.home() / ".config" / "bogoseo" / "settings.yaml"]
    env = os.environ.get("BOGOSEO_SETTINGS")
    if env:
        paths.append(Path(env))
    for p in paths:
        merged = _deep_merge(merged, _load_yaml(p))
    return merged


def keys_path() -> Path:
    env = os.environ.get("BOGOSEO_KEYS")
    return Path(env) if env else Path.home() / ".config" / "bogoseo" / "keys.yaml"


def load_keys() -> dict:
    d = _load_yaml(keys_path())
    return {k: v for k, v in d.items() if v not in (None, "")}
