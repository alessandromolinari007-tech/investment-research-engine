"""The UI and the change monitor must use the classification constants of ire/scoring.py, never copies of the strings."""
import os
import re
from pathlib import Path

import pandas as pd

import ire.scoring as S

ROOT = Path(__file__).resolve().parents[1]
CLASS_CONSTANTS = {k: v for k, v in vars(S).items() if re.fullmatch(r"C_[A-Z]+", k) and isinstance(v, str)}


def test_no_duplicated_class_strings():
    for rel in ("app/app.py", "app/data.py", "ire/changes.py", "ire/portfolio.py", "ire/thesis.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for name, value in CLASS_CONSTANTS.items():
            assert f'"{value}"' not in src, f"{rel} duplica l'etichetta {name}: importare la costante"


def test_every_class_has_an_icon():
    src = (ROOT / "app/app.py").read_text(encoding="utf-8")
    block = src[src.index("CLASS_ICON = {"):src.index("}", src.index("CLASS_ICON = {"))]
    for name in CLASS_CONSTANTS:
        assert f"{name}:" in block, f"CLASS_ICON senza {name}"


def test_home_quality_discount_section_is_filled(world):
    """Regression: the '💎' section was always empty because it filtered on old labels."""
    from streamlit.testing.v1 import AppTest

    from ire.db import connect
    from ire.pipeline import Pipeline

    rid = Pipeline(mode="quick", verbose=False).run()
    sc = pd.read_sql_query("SELECT classification FROM scores WHERE run_id=?", connect(), params=[rid])
    classes = set(sc["classification"])
    assert classes & S.QUALITY_DISCOUNT, "il mondo sintetico deve produrre almeno una 'qualità a sconto'"
    expected_tables = sum(bool(classes & group) for group in (S.QUALITY_DISCOUNT, {S.C_QFAIR}, S.NEGATIVE_CLASSES))

    at = _page("home")
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.dataframe) == expected_tables
    assert any(s.value.startswith("💎") for s in at.subheader)


def _page(page: str, monkeypatch=None):
    """Renders one page. With `monkeypatch` the page stays selected for later interactions (clicks)."""
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    st.cache_data.clear()          # every test has its own database: no cached query results across tests
    if monkeypatch is not None:
        monkeypatch.setenv("IRE_TEST_PAGE", page)
        at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=180)
        return at.run()
    os.environ["IRE_TEST_PAGE"] = page
    try:
        at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=180)
        at.run()
    finally:
        os.environ.pop("IRE_TEST_PAGE", None)
    return at


def test_analyze_my_portfolio_does_not_crash(world, monkeypatch):
    """Bug §7.6: 'Analizza il MIO portafoglio' always failed (`if hc:` on a pandas Series)."""
    from ire.db import connect
    from ire.pipeline import Pipeline

    rid = Pipeline(mode="quick", verbose=False).run()
    con = connect()
    tick = [r[0] for r in con.execute("SELECT c.ticker FROM scores s JOIN companies c USING(company_id) "
                                      "WHERE s.run_id=? AND s.robust_score IS NOT NULL LIMIT 3", (rid,))]
    con.executemany("INSERT INTO user_portfolio (ticker, weight, note) VALUES (?,?,?)",
                    [(t, 1.0, "") for t in tick] + [("NOPE.XX", 1.0, "")])
    con.commit()
    at = _page("portfolio", monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    btn = next(b for b in at.button if "Analizza" in b.label)
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("NOPE.XX" in w.value for w in at.warning)              # missing prices are reported, not hidden
    assert any(s.value == "Le tue posizioni viste dal sistema" for s in at.subheader)


def test_failed_run_is_visible(world):
    from ire.db import connect
    from ire.pipeline import Pipeline

    Pipeline(mode="quick", verbose=False).run()
    con = connect()
    con.execute("INSERT INTO runs (started_at, mode, status, summary) VALUES ('2026-01-01T00:00:00', 'quick', 'failed', ?)",
                ('{"error": "SEC non raggiungibile"}',))
    con.commit()
    at = _page("home")
    assert not at.exception
    assert any("fallita" in w.value and "SEC non raggiungibile" in w.value for w in at.warning)


def test_methodology_tab_shows_document(world):
    from ire.pipeline import Pipeline

    Pipeline(mode="quick", verbose=False).run()
    at = _page("data")
    assert not at.exception
    assert any("# Metodologia" in m.value for m in at.markdown)
