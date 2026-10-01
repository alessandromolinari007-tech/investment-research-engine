"""Configuration loading. Single source of truth for paths and parameters."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("IRE_CONFIG", ROOT / "config.toml"))


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # ---- paths -----------------------------------------------------------
    @property
    def data_dir(self) -> Path:
        d = Path(self.get("general.data_dir", "data"))
        if not d.is_absolute():
            d = ROOT / d
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cache_dir(self) -> Path:
        d = self.data_dir / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "research.sqlite"

    # ---- convenience -----------------------------------------------------
    @property
    def base_currency(self) -> str:
        return str(self.get("general.base_currency", "EUR")).upper()

    @property
    def universe_mode(self) -> str:
        return str(self.get("universe.mode", "standard"))

    def min_market_cap_usd(self, mode: str | None = None) -> float:
        mode = mode or self.universe_mode
        return float(self.get(f"universe.min_market_cap_usd.{mode}", 2e9))

    @property
    def sec_user_agent(self) -> str:
        ua = os.environ.get("IRE_SEC_USER_AGENT") or self.get("sec.user_agent", "")
        return str(ua).strip()


_CONFIG: Config | None = None


def load_config(path: Path | None = None, reload: bool = False) -> Config:
    global _CONFIG
    if _CONFIG is not None and not reload and path is None:
        return _CONFIG
    p = Path(path) if path else CONFIG_PATH
    raw: dict[str, Any] = {}
    if p.exists():
        with open(p, "rb") as fh:
            raw = tomllib.load(fh)
    cfg = Config(raw=raw)
    if path is None:
        _CONFIG = cfg
    return cfg
