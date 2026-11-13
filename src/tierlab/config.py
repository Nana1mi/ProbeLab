"""Configuration loader and management for TierLab."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import yaml

from tierlab.exceptions import ConfigurationError
from tierlab.types import (
    Budget,
    ControllerConfig,
    ModelEndpointConfig,
    ModelsConfigFile,
    PolicyType,
)


def compute_config_hash(data: dict[str, Any]) -> str:
    """Compute a deterministic short MD5 hash of configuration dict."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()[:10]


def find_config_file(filename: str, search_dirs: list[Path] | None = None) -> Path:
    """Find a configuration file by name across possible directories."""
    candidate_paths = []
    if search_dirs:
        candidate_paths.extend([d / filename for d in search_dirs])

    candidate_paths.extend([
        Path(filename),
        Path("configs") / filename,
        Path(__file__).resolve().parent.parent.parent / "configs" / filename,
    ])

    for p in candidate_paths:
        if p.is_file():
            return p.resolve()

    raise ConfigurationError(f"Could not locate configuration file: {filename}")


def load_models_config(path: str | Path | None = None) -> ModelsConfigFile:
    """Load and parse configs/models.yaml."""
    if path is None:
        p = find_config_file("models.yaml")
    else:
        p = Path(path)
        if not p.is_file():
            p = find_config_file(str(path))

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        raise ConfigurationError(f"Failed to read models config from {p}: {e}") from e

    try:
        return ModelsConfigFile(**data)
    except Exception as e:
        raise ConfigurationError(f"Invalid models configuration structure in {p}: {e}") from e


def load_run_config(
    config_path: str | Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load default/custom run configuration and apply CLI/programmatic overrides."""
    data: dict[str, Any] = {}
    if config_path:
        p = Path(config_path)
        if not p.is_file():
            p = find_config_file(str(config_path))
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        try:
            p = find_config_file("default.yaml")
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except ConfigurationError:
            data = {}

    if overrides:
        for k, v in overrides.items():
            if v is not None:
                if isinstance(v, dict) and isinstance(data.get(k), dict):
                    data[k].update(v)
                else:
                    data[k] = v

    return data


def load_batch_config(config_path: str | Path) -> dict[str, Any]:
    """Load a batch experiment yaml configuration."""
    p = Path(config_path)
    if not p.is_file():
        p = find_config_file(str(config_path))
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception as e:
        raise ConfigurationError(f"Failed to load batch config {p}: {e}") from e
