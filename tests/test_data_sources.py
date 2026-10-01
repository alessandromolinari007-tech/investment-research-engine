"""Unit tests for the price store, the Yahoo wrapper and the FX table. yfinance is SIMULATED (monkeypatch):
the price series below are synthetic test inputs, never shown as market data."""
import time

import numpy as np
import pandas as pd
import pytest

import ire.prices as P
import ire.sources.yahoo as Y
from ire.db import init_db
from ire.sources.fx_macro import FxTable


# ---------------------------------------------------------------------------------------------- helpers
def _series(start="2014-01-01", end=None, base=100.0):
    idx = pd.bdate_range(start, end or pd.Timestamp.today().normalize())
    close = base * (1 + 0.0002) ** np.arange(len(idx))
    return pd.DataFrame({"close": close, "adj_close": close * 0.9, "volume": 1e6}, index=idx)


class FakeYahoo:
    """Stands in for yahoo.download_prices; records every call."""

    def __init__(self, frames):
        self.frames = frames          # ticker -> full DataFrame
        self.calls = []

    def __call__(self, tickers, start="2014-01-01", batch=50, actions=False):
        self.calls.append({"tickers": list(tickers), "start": start, "actions": actions})
        out = {}
        for t in tickers:
            if t in self.frames:
                df = self.frames[t][self.frames[t].index >= pd.Timestamp(start)].copy()
                if actions and "splits" not in df.columns:
                    df["splits"] = 0.0
                if not actions:
                    df = df.drop(columns=["splits"], errors="ignore")
                out[t] = df
        return out


@pytest.fixture()
def store(tmp_path, monkeypatch):
    con = init_db(tmp_path / "t.sqlite")
    fake = FakeYahoo({"AAA": _series(), "BBB": _series(base=50)})
    monkeypatch.setattr(P.yahoo, "download_prices", fake)
    s = P.PriceStore(con, progress=lambda m: None)
    return s, fake, con


def _meta(con, t):
    return con.execute("SELECT * FROM price_meta WHERE ticker=?", (t,)).fetchone()


# ---------------------------------------------------------------------------------------------- PriceStore
def test_full_download_then_cached(store):
    s, fake, con = store
    out = s.get_full(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert fake.calls[-1]["actions"] is True
    assert _meta(con, "AAA")["full_fetched_at"] is not None
    n = len(fake.calls)
    s.get_full(["AAA", "BBB"])
    assert len(fake.calls) == n          # nothing downloaded: full and recent are both fresh


def test_full_history_is_refreshed_after_ttl(store):
    s, fake, con = store
    s.get_full(["AAA"])
    con.execute("UPDATE price_meta SET full_fetched_at=?", (time.time() - 30 * 86400,))
    s.get_full(["AAA"])
    assert fake.calls[-1]["start"] == P.FULL_START and fake.calls[-1]["actions"] is True


def test_recent_refresh_does_not_make_full_history_fresh(store):
    """Bug §7.1: get_recent updated fetched_at and get_full believed the 10-year history was there."""
    s, fake, con = store
    s.get_recent(["AAA"], start="2025-01-01")
    m = _meta(con, "AAA")
    assert m["full_fetched_at"] is None and m["requested_start"] == "2025-01-01"
    out = s.get_full(["AAA"])
    assert fake.calls[-1]["start"] == P.FULL_START
    assert out["AAA"].index.min() <= pd.Timestamp("2014-01-03")


def test_incremental_top_up_keeps_full_history(store):
    s, fake, con = store
    s.get_full(["AAA"])
    old = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
    con.execute("UPDATE price_meta SET fetched_at=?", (old.isoformat(),))
    s.get_full(["AAA"])
    last = fake.calls[-1]
    assert last["start"] > "2020-01-01"              # only the recent window
    m = _meta(con, "AAA")
    assert m["requested_start"] == P.FULL_START      # still a consistent full history
    assert m["full_fetched_at"] is not None


def test_split_triggers_full_redownload(store):
    """Bug §7.1: after a split Yahoo re-adjusts the whole history; the stored one must be replaced."""
    s, fake, con = store
    s.get_full(["AAA"])
    con.execute("UPDATE price_meta SET fetched_at=?", ((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)).isoformat(),))
    split_day = fake.frames["AAA"].index[-3]
    new = fake.frames["AAA"].copy()
    new[["close", "adj_close"]] = new[["close", "adj_close"]] / 2     # 2:1 split, whole history re-adjusted
    new["splits"] = 0.0
    new.loc[split_day, "splits"] = 2.0
    fake.frames["AAA"] = new
    out = s.get_full(["AAA"])
    assert fake.calls[-1]["start"] == P.FULL_START   # full re-download after the incremental check
    stored = out["AAA"]["close"]
    assert stored.iloc[0] == pytest.approx(new["close"].iloc[0])   # old part consistent with the new adjustment
    assert s.splits("AAA") == [(split_day.strftime("%Y-%m-%d"), 2.0)]


def test_dividend_readjustment_detected_by_recent_refresh(store):
    s, fake, con = store
    s.get_full(["AAA"])
    con.execute("UPDATE price_meta SET fetched_at=?", ((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)).isoformat(),))
    fake.frames["AAA"] = fake.frames["AAA"].assign(adj_close=lambda d: d["adj_close"] * 0.98)   # dividend: adj_close re-based
    s.get_recent(["AAA"], start="2025-01-01")
    m = _meta(con, "AAA")
    assert m["full_fetched_at"] is None            # next get_full will download the whole history again
    first = con.execute("SELECT MIN(date) FROM prices WHERE ticker='AAA'").fetchone()[0]
    assert first <= "2014-01-03"                   # history KEPT until the full download succeeds (red team 2)
    s.get_full(["AAA"])
    stored = s._read(["AAA"], "2014-01-01")["AAA"]["adj_close"]
    assert stored.iloc[0] == pytest.approx(fake.frames["AAA"]["adj_close"].iloc[0])


def test_failed_full_redownload_keeps_history(store, monkeypatch):
    s, fake, con = store
    s.get_full(["AAA"])
    con.execute("UPDATE price_meta SET fetched_at=?", ((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)).isoformat(),))
    fake.frames["AAA"] = fake.frames["AAA"].assign(adj_close=lambda d: d["adj_close"] * 0.98)
    real = fake.__call__

    def flaky(tickers, start="2014-01-01", batch=50, actions=False):
        return {} if start == P.FULL_START else real(tickers, start, batch, actions)

    monkeypatch.setattr(P.yahoo, "download_prices", flaky)
    out = s.get_full(["AAA"])
    assert out["AAA"].index.min() <= pd.Timestamp("2014-01-03")      # nothing lost when Yahoo returns nothing
    r = out["AAA"]["adj_close"].pct_change().dropna()
    assert r.abs().max() < 0.01                                       # no jump between old and new basis


def test_today_bar_is_not_stored(store):
    s, fake, con = store
    today = pd.Timestamp.today().normalize()
    fake.frames["AAA"].loc[today] = [101.0, 90.9, 5e5]
    s.get_full(["AAA"])
    last = con.execute("SELECT MAX(date) FROM prices WHERE ticker='AAA'").fetchone()[0]
    assert last < today.strftime("%Y-%m-%d")


def test_empty_download_is_not_marked_fresh(store):
    """Bug §7.2: an empty answer (silent rate limit) must not count as fresh data."""
    s, fake, con = store
    s.get_full(["ZZZ"])
    assert _meta(con, "ZZZ") is None
    assert s.last_empty == ["ZZZ"]
    n = len(fake.calls)
    s.get_full(["ZZZ"])
    assert len(fake.calls) == n + 1                # retried at the next call


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_download_and_commit_by_batch(store, monkeypatch):
    s, fake, con = store
    monkeypatch.setattr(P, "STORE_BATCH", 1)
    commits = []
    real_commit = con.commit

    class Spy:
        def __getattr__(self, k):
            return getattr(con, k)

        def commit(self):
            commits.append(1)
            real_commit()

    s.con = Spy()
    s.get_full(["AAA", "BBB"])
    assert len([c for c in fake.calls if c["actions"]]) == 2     # one download per batch
    assert len(commits) >= 2                                     # one commit per batch


# ---------------------------------------------------------------------------------------------- Yahoo wrapper
class FakeYF:
    def __init__(self, empty_first=1):
        self.n = 0
        self.empty_first = empty_first
        self.threads = []

    def download(self, chunk, start, auto_adjust, actions, group_by, threads, progress, timeout):
        self.n += 1
        self.threads.append(threads)
        idx = pd.bdate_range("2025-01-01", periods=5)
        if self.n <= self.empty_first:
            return pd.DataFrame()                      # what yfinance returns when rate-limited
        cols = pd.MultiIndex.from_product([chunk, ["Close", "Adj Close", "Volume"]])
        return pd.DataFrame(1.0, index=idx, columns=cols)


def test_download_prices_retries_when_batch_mostly_empty(monkeypatch):
    fake = FakeYF(empty_first=1)
    monkeypatch.setattr(Y, "yf", lambda: fake)
    monkeypatch.setattr(Y.time, "sleep", lambda s: None)
    monkeypatch.setattr(Y.THROTTLE, "wait", lambda: None)
    out = Y.download_prices([f"T{i}" for i in range(6)], start="2025-01-01")
    assert len(out) == 6 and fake.n == 2
    assert fake.threads == [False, False]


def test_empty_statements_and_info_are_not_cached(tmp_path, monkeypatch):
    class Cfg:
        cache_dir = tmp_path

    monkeypatch.setattr(Y, "load_config", lambda: Cfg)
    monkeypatch.setattr(Y.THROTTLE, "wait", lambda: None)

    class T:
        def __init__(self, t):
            self.income_stmt = self.balance_sheet = self.cashflow = pd.DataFrame()
            self.quarterly_income_stmt = self.quarterly_balance_sheet = self.quarterly_cashflow = pd.DataFrame()

        def get_info(self):
            return {}

    class YF:
        Ticker = T

    monkeypatch.setattr(Y, "yf", lambda: YF)
    assert Y.statements("AAA") is None
    assert Y.info("AAA") is None
    assert not (tmp_path / "yahoo" / "statements" / "AAA.json").exists()
    assert not (tmp_path / "yahoo" / "info" / "AAA.json").exists()


def test_cache_path_avoids_windows_reserved_names(tmp_path, monkeypatch):
    class Cfg:
        cache_dir = tmp_path

    monkeypatch.setattr(Y, "load_config", lambda: Cfg)
    assert Y._cache_path("info", "CON").name == "_CON.json"
    assert Y._cache_path("info", "NUL.L").name == "_NUL.L.json"
    assert Y._cache_path("info", "AAPL").name == "AAPL.json"


# ---------------------------------------------------------------------------------------------- FX
def _fx():
    rows = []
    for d in pd.bdate_range("2022-01-03", "2022-03-31"):
        rows.append({"date": d.strftime("%Y-%m-%d"), "currency": "USD", "per_eur": 1.1, "source": "test"})
        if d <= pd.Timestamp("2022-03-01"):
            rows.append({"date": d.strftime("%Y-%m-%d"), "currency": "RUB", "per_eur": 90.0, "source": "test"})
    return FxTable(pd.DataFrame(rows))


def test_dead_currency_is_not_current():
    """Bug §7.4: forward-fill made a currency no longer published look fresh."""
    fx = _fx()
    assert fx.has("USD") and not fx.has("RUB")
    assert fx.rate("RUB") is None
    assert fx.rate("RUB", "2022-03-05") == pytest.approx(90.0)     # within 10 days of the last observation
    assert fx.rate("RUB", "2022-03-20") is None
    assert fx.convert(100.0, "USD", "EUR") == pytest.approx(100 / 1.1)


def test_series_to_eur_stops_after_gap():
    fx = _fx()
    px = pd.Series(90.0, index=pd.bdate_range("2022-02-20", "2022-03-25"))
    eur = fx.series_to_eur(px, "RUB")
    assert eur.loc["2022-02-25"] == pytest.approx(1.0)
    assert eur.loc["2022-03-08"] == pytest.approx(1.0)
    assert np.isnan(eur.loc["2022-03-25"])


def test_nikkei_alphanumeric_codes_are_kept():
    from ire.sources.universe_intl import INDICES, _clean_symbol

    nk = next(i for i in INDICES if i.name == "Nikkei 225")
    assert _clean_symbol("7203", nk) == "7203.T"
    assert _clean_symbol("TYO: 285A", nk) == "285A.T"
    assert _clean_symbol("ABCD", nk) is None


def test_yahoo_pair_does_not_make_ecb_currencies_stale():
    rows = [{"date": d.strftime("%Y-%m-%d"), "currency": "USD", "per_eur": 1.1, "source": "ECB"}
            for d in pd.bdate_range("2022-01-03", "2022-03-01")]
    rows += [{"date": d.strftime("%Y-%m-%d"), "currency": "TWD", "per_eur": 33.0, "source": "Yahoo EURTWD=X"}
             for d in pd.bdate_range("2022-01-03", "2022-04-29")]
    fx = FxTable(pd.DataFrame(rows))
    assert fx.has("USD") and fx.has("TWD")
    assert fx.convert(110.0, "USD", "EUR") == pytest.approx(100.0)


def test_yahoo_currency_older_than_ecb_is_stale():
    rows = [{"date": d.strftime("%Y-%m-%d"), "currency": "USD", "per_eur": 1.1, "source": "ECB"}
            for d in pd.bdate_range("2026-01-05", "2026-09-30")]
    rows += [{"date": d.strftime("%Y-%m-%d"), "currency": "TWD", "per_eur": 35.0, "source": "Yahoo Finance EURTWD=X"}
             for d in pd.bdate_range("2026-01-05", "2026-05-29")]
    fx = FxTable(pd.DataFrame(rows))
    assert fx.has("USD") and not fx.has("TWD")          # → stage_fx_extra downloads it again
    assert fx.rate("TWD") is None


def test_sec_rate_limit_page_is_not_a_user_agent_error(tmp_path, monkeypatch):
    """Verifier: SEC's rate-limit page also mentions 'undeclared automated tools' and the user agent."""
    import ire.http as H

    c = H.HttpClient(user_agent="Nome Cognome nome@example.com", cache_dir=tmp_path)

    class R:
        status_code = 403
        text = ("<title>SEC.gov | Request Rate Threshold Exceeded</title> Your request originates from an undeclared "
                "automated tool. Please declare your traffic by updating your user agent.")
        headers = {}

    monkeypatch.setattr(c.session, "get", lambda *a, **k: R())
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    monkeypatch.setattr(c, "_cooldown_until", {})
    with pytest.raises(H.SourceUnavailable) as e:
        c.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json", max_retries=2)
    assert "limite di richieste" in str(e.value) and "User-Agent" not in str(e.value)


def test_isolated_empty_ticker_is_retried_at_the_end(monkeypatch):
    """Real run (old version): valid tickers like NOVO.CO came back empty ("no timezone found") in batches where
    most tickers were fine, so the batch retry never fired and they were excluded from the universe."""
    calls = []

    def chunk(tickers, start, actions):
        calls.append(list(tickers))
        idx = pd.bdate_range("2025-01-01", periods=5)
        df = pd.DataFrame({"close": 1.0, "adj_close": 1.0, "volume": 1.0}, index=idx)
        return {t: df for t in tickers if not (t == "NOVO.CO" and len(calls) == 1)}

    monkeypatch.setattr(Y, "_download_chunk", chunk)
    monkeypatch.setattr(Y.time, "sleep", lambda s: None)
    out = Y.download_prices([f"T{i}" for i in range(9)] + ["NOVO.CO"], start="2025-01-01")
    assert "NOVO.CO" in out and calls[-1] == ["NOVO.CO"]
