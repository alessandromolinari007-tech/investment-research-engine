"""Render every Streamlit page with AppTest against the DB built by dev_offline_run.py.
Usage: IRE_CONFIG=/tmp/xxxx/config.toml python scripts/dev_ui_test.py"""
import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

assert os.environ.get("IRE_CONFIG"), "imposta IRE_CONFIG con il config.toml stampato da dev_offline_run.py"
app = str(Path(__file__).resolve().parents[1] / "app" / "app.py")
for page in ["home", "company", "compare", "screener", "portfolio", "changes", "learn", "backtest", "data"]:
    os.environ["IRE_TEST_PAGE"] = page
    at = AppTest.from_file(app, default_timeout=180)
    if page == "compare":
        at.session_state["cid"] = "CIK0001000006"   # synthetic company id from fake_world
    at.run()
    print(page, "exceptions:", [e.value for e in at.exception][:3], "| markdown blocks:", len(at.markdown))
