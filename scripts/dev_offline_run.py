"""Run the full pipeline OFFLINE on the SYNTHETIC world of tests/fixtures (no network).
Prints the temporary data folder; reuse it with dev_ui_test.py (IRE_CONFIG)."""
import os
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
tmp = tempfile.mkdtemp()
text = (root / "config.toml").read_text(encoding="utf-8")
text = (text.replace('data_dir = "data"', f'data_dir = "{tmp}/data"')
            .replace('user_agent = ""', 'user_agent = "T t@e.com"')
            .replace("quick = 20_000_000_000", "quick = 1_000_000_000"))
Path(tmp, "config.toml").write_text(text, encoding="utf-8")
os.environ["IRE_CONFIG"] = f"{tmp}/config.toml"
import ire.config as C  # noqa: E402

C.CONFIG_PATH = Path(f"{tmp}/config.toml")
C._CONFIG = None
from tests.fixtures.fake_world import FakeWorld  # noqa: E402

w = FakeWorld()
import ire.pipeline as P  # noqa: E402
import ire.sources.fx_macro as FXM  # noqa: E402
import ire.sources.sec as S  # noqa: E402
import ire.sources.universe_intl as UI  # noqa: E402
import ire.sources.yahoo as Y  # noqa: E402

for mod, names in ((S, ["company_tickers", "submissions", "companyfacts", "frame", "fetch_document"]),
                   (Y, ["download_prices", "info", "statements", "fx_history"]),
                   (FXM, ["ecb_history", "fred_series"]), (UI, ["international_candidates"]),
                   (P, ["ecb_history", "fred_series"])):
    for n in names:
        setattr(mod, n, getattr(w, n))
rid = P.Pipeline(mode="quick", verbose=True).run()
print("DATA", tmp)
