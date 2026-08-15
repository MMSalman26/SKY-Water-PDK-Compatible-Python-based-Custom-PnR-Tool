"""Configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG = Path(__file__).resolve().parent / "defaults.yaml"


def package_root() -> Path:
    return _PACKAGE_ROOT


def project_root() -> Path:
    return _PACKAGE_ROOT.parent


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base (dicts only)."""
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict[str, Any]:
    with _DEFAULT_CONFIG.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if path is not None:
        with Path(path).open("r", encoding="utf-8") as fh:
            override = yaml.safe_load(fh) or {}
        if not isinstance(override, dict):
            raise ValueError(f"Config {path} must be a YAML mapping")
        data = _deep_merge(data, override)
    # Resolve PDK cache to an absolute path under the package by default
    cache = data.get("pdk", {}).get("cache_dir", "data")
    cache_path = Path(cache)
    if not cache_path.is_absolute():
        cache_path = _PACKAGE_ROOT / cache_path
    data.setdefault("pdk", {})["cache_dir"] = str(cache_path)
    return data
