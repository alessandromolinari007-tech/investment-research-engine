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

    os.environ["IRE_TEST_PAGE"] = "home"
    try:
        at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=180)
        at.run()
    finally:
        os.environ.pop("IRE_TEST_PAGE", None)
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.dataframe) == expected_tables
    assert any(s.value.startswith("💎") for s in at.subheader)
