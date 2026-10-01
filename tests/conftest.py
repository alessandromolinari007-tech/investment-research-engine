"""Shared fixtures: the SYNTHETIC offline world (no network)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    root = os.path.dirname(os.path.dirname(__file__))
    text = open(os.path.join(root, "config.toml"), encoding="utf-8").read()
    text = text.replace('data_dir = "data"', f'data_dir = "{(tmp_path / "data").as_posix()}"')
    text = text.replace('user_agent = ""', 'user_agent = "Test User test@example.com"')
    text = text.replace("quick = 20_000_000_000", "quick = 1_000_000_000")
    cfg.write_text(text, encoding="utf-8")
    monkeypatch.setenv("IRE_CONFIG", str(cfg))
    import ire.config as C

    C.CONFIG_PATH = cfg
    C._CONFIG = None
    import ire.http as H

    H._CLIENT = None
    from tests.fixtures.fake_world import FakeWorld

    w = FakeWorld()
    import ire.pipeline as P
    import ire.risk  # noqa: F401
    import ire.sources.fx_macro as FXM
    import ire.sources.sec as S
    import ire.sources.universe_intl as UI
    import ire.sources.yahoo as Y

    for mod, names in ((S, ["company_tickers", "submissions", "companyfacts", "frame", "fetch_document"]),
                       (Y, ["download_prices", "info", "statements", "fx_history"]),
                       (FXM, ["ecb_history", "fred_series"]), (UI, ["international_candidates"]),
                       (P, ["ecb_history", "fred_series"])):
        for n in names:
            monkeypatch.setattr(mod, n, getattr(w, n))
    return w
