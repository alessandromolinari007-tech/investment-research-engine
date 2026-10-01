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

MODES = ("quick", "standard", "full")


class ConfigError(Exception):
    """Configuration problem explained in Italian (shown to the user without a traceback)."""


def local_config_path(path: Path | None = None) -> Path:
    """`config.local.toml` next to config.toml: personal settings (SEC contact), never versioned."""
    return Path(path or CONFIG_PATH).with_name("config.local.toml")


def _read_toml(p: Path) -> dict[str, Any]:
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):          # UTF-8 with BOM (Notepad "UTF-8 con BOM")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")   # saved as "ANSI" by Notepad: Windows-1252 on Italian PCs
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        hint = ""
        if "escape" in str(e).lower() or "\\" in p.read_text(encoding="utf-8", errors="replace"):
            hint = (" Probabile causa: un percorso Windows con '\\' tra virgolette doppie. Usa le barre normali "
                    "(C:/Users/nome/dati) oppure gli apici singoli ('C:\\Users\\nome\\dati').")
        raise ConfigError(f"Errore di sintassi in {p.name}: {e}.{hint}") from None


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def validate(cfg: Config) -> list[str]:
    """Values that would make the analysis silently wrong. Returns the problems (empty list = ok)."""
    errs: list[str] = []

    def num(key: str, lo: float | None = None, hi: float | None = None, required: bool = False):
        v = cfg.get(key)
        if v is None:
            if required:
                errs.append(f"{key}: valore mancante")
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errs.append(f"{key} = {v!r}: deve essere un numero")
            return None
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            rng = f"tra {lo} e {hi}" if lo is not None and hi is not None else (f">= {lo}" if lo is not None else f"<= {hi}")
            errs.append(f"{key} = {v}: deve essere {rng}")
            return None
        return float(v)

    mode = cfg.get("universe.mode", "standard")
    if mode not in MODES:
        errs.append(f"universe.mode = {mode!r}: valori ammessi {', '.join(MODES)}")
    if str(cfg.get("general.base_currency", "EUR")).upper() != "EUR":
        errs.append("general.base_currency: è supportato solo \"EUR\"")
    for m in MODES:
        num(f"universe.min_market_cap_usd.{m}", 0)
    num("universe.min_median_dollar_volume_usd", 0)
    num("sec.max_requests_per_second", 0.1, 10)
    for k in ("cache.fundamentals_ttl_days", "cache.prices_ttl_hours", "cache.prices_full_ttl_days", "cache.filings_ttl_days"):
        num(k, 0)
    num("valuation.equity_risk_premium", 0, 0.15)
    num("valuation.terminal_growth", -0.02, 0.05)
    num("valuation.dcf_years", 3, 30)
    num("scoring.min_peer_group", 3)
    num("scoring.deep_analysis_top_n", 0)
    num("scoring.deep_analysis_max", 0)
    w = cfg.get("scoring.weights", {}) or {}
    vals = [num(f"scoring.weights.{k}", 0) for k in w]
    if w and sum(v or 0 for v in vals) <= 0:
        errs.append("scoring.weights: la somma dei pesi deve essere maggiore di 0")
    tp = num("portfolio.target_positions", 1, 200)
    mx = num("portfolio.max_weight", 0.001, 1)
    mn = num("portfolio.min_weight", 0, 1)
    num("portfolio.max_sector_weight", 0.01, 1)
    num("portfolio.max_pair_correlation", -1, 1)
    num("portfolio.correlation_penalty", 0)
    num("portfolio.min_robust_percentile", 0, 100)
    mp = num("portfolio.min_positions", 1, 200)
    num("portfolio.no_trade_band", 0, 1)
    num("portfolio.min_weeks_3y", 1, 156)
    if mx is not None and mn is not None and mn > mx:
        errs.append(f"portfolio.min_weight ({mn}) è maggiore di portfolio.max_weight ({mx})")
    if mp is not None and tp is not None and mp > tp:
        errs.append(f"portfolio.min_positions ({mp:.0f}) è maggiore di portfolio.target_positions ({tp:.0f})")
    for k in (cfg.get("portfolio.max_region_weight", {}) or {}):
        num(f"portfolio.max_region_weight.{k}", 0, 1)
    return errs


def load_config(path: Path | None = None, reload: bool = False) -> Config:
    """config.toml + config.local.toml (personal overrides, not versioned). Raises ConfigError if invalid."""
    global _CONFIG
    if _CONFIG is not None and not reload and path is None:
        return _CONFIG
    p = Path(path) if path else CONFIG_PATH
    raw: dict[str, Any] = _read_toml(p) if p.exists() else {}
    local = local_config_path(p)
    if local.exists():
        raw = _merge(raw, _read_toml(local))
    cfg = Config(raw=raw)
    errs = validate(cfg)
    if errs:
        raise ConfigError("Valori non validi nella configurazione:\n  - " + "\n  - ".join(errs))
    if path is None:
        _CONFIG = cfg
    return cfg
