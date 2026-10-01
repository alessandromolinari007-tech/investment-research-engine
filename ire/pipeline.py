"""End-to-end pipeline:

 macro (FX, rates) → universe → prices → fundamentals (SEC XBRL / Yahoo) → per-company analysis
 → sector-relative scoring → deep filing analysis on the shortlist → portfolio construction
 → snapshot & diff vs previous run.

Run with:  python -m ire run [--mode quick|standard|full] [--tickers T1,T2] [--skip-deep]
"""
from __future__ import annotations

import json
import math
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from . import classify, qualitative, scoring
from .analysis import analyze_company
from .config import load_config
from .db import init_db, log, now_iso, upsert, upsert_merge
from .http import SourceUnavailable
from .normalize.model import Financials
from .normalize.sec_facts import normalize_companyfacts
from .normalize.yahoo_facts import normalize_yahoo
from .portfolio import analyze_portfolio, construct_portfolio, serializable
from .prices import PriceStore, main_unit
from .risk import BENCHMARKS, weekly_returns
from .sources import sec, yahoo
from .sources.fx_macro import RATE_SERIES, FxTable, ecb_history, fred_series
from .thesis import build_thesis
from .universe import build_universe, candidate_rows


class Pipeline:
    def __init__(self, mode: str | None = None, extra_tickers: list[str] | None = None, skip_deep: bool = False,
                 limit: int | None = None, verbose: bool = True):
        self.cfg = load_config()
        self.mode = mode or self.cfg.universe_mode
        self.skip_deep = skip_deep
        self.limit = limit
        self.verbose = verbose
        self.con = init_db()
        self._migrate()
        self.extra = [t.strip().upper() for t in (extra_tickers or []) if t.strip()]
        self.extra += [r["ticker"] for r in self.con.execute("SELECT ticker FROM watchlist")]
        self.extra += [r["ticker"] for r in self.con.execute("SELECT ticker FROM user_portfolio")]
        self.run_id: int | None = None
        self.t0 = time.time()
        self.fins: dict[str, Financials] = {}
        self.infos: dict[str, dict[str, Any]] = {}
        self.stats: dict[str, Any] = {}

    def _migrate(self) -> None:
        wanted = {"price_meta": {"requested_start": "TEXT", "full_fetched_at": "REAL"},
                  "companies": {"liquidity_usd": "REAL", "market_cap_usd": "REAL", "forced": "INTEGER DEFAULT 0"}}
        for table, add in wanted.items():
            cols = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
            for col, typ in add.items():
                if col not in cols:
                    self.con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        # runs left 'running' by a crash / closed window are marked as interrupted
        self.con.execute("UPDATE runs SET status='interrupted', finished_at=COALESCE(finished_at, ?) WHERE status='running'",
                         (now_iso(),))
        self.con.commit()

    # ------------------------------------------------------------------ utils
    def say(self, msg: str, level: str = "info", stage: str = "") -> None:
        elapsed = time.time() - self.t0
        line = f"[{elapsed / 60:5.1f} min] {msg}"
        if self.verbose:
            print(line, flush=True)
        log(self.con, self.run_id, level, stage, msg)

    # ------------------------------------------------------------------ main
    def run(self) -> int:
        cur = self.con.execute("INSERT INTO runs (started_at, mode, status, params) VALUES (?,?,?,?)",
                               (now_iso(), self.mode, "running",
                                json.dumps({"extra": self.extra, "skip_deep": self.skip_deep, "limit": self.limit})))
        self.run_id = cur.lastrowid
        self.con.commit()
        self.say(f"Avvio analisi #{self.run_id} (modalità {self.mode})")
        try:
            self.stage_macro()
            self.stage_universe()
            self.stage_prices()
            self.stage_fundamentals()
            self.stage_fx_extra()
            self.stage_analysis()
            self.stage_scoring()
            if not self.skip_deep:
                self.stage_deep()
            self.stage_portfolio()
            self.stage_diff()
            self.con.execute("UPDATE runs SET status='completed', finished_at=?, summary=? WHERE run_id=?",
                             (now_iso(), json.dumps(self.stats, default=str), self.run_id))
            self.con.commit()
            self._prune()
            self.say(f"Analisi completata in {(time.time() - self.t0) / 60:.1f} minuti. "
                     f"Avvia l'interfaccia con: run_app.bat (oppure: python -m ire app)")
        except BaseException as e:  # noqa: BLE001  (also Ctrl+C / closed window → 'interrupted')
            interrupted = isinstance(e, (KeyboardInterrupt, SystemExit))
            status = "interrupted" if interrupted else "failed"
            msg = "Analisi interrotta dall'utente" if interrupted else f"ERRORE: {e}\n{traceback.format_exc()}"
            try:
                self.say(msg, "error")
                self.con.execute("UPDATE runs SET status=?, finished_at=?, summary=? WHERE run_id=?",
                                 (status, now_iso(), json.dumps({"error": str(e) or status, **self.stats}, default=str),
                                  self.run_id))
                self.con.commit()
            except Exception:  # noqa: BLE001
                pass
            raise
        return self.run_id

    # ------------------------------------------------------------------ stages
    def stage_macro(self) -> None:
        self.say("1/9 Cambi (BCE) e tassi d'interesse (FRED)…", stage="macro")
        try:
            fx = ecb_history()
            fx = fx[fx["date"] >= "2013-01-01"]
            self.con.executemany("INSERT OR REPLACE INTO fx (date, currency, per_eur, source) VALUES (?,?,?,?)",
                                 fx[["date", "currency", "per_eur", "source"]].itertuples(index=False, name=None))
            self.con.commit()
            self.say(f"   BCE: {fx['currency'].nunique()} valute, ultimo giorno {fx['date'].max()}")
        except Exception as e:  # noqa: BLE001
            self.say(f"   BCE non raggiungibile ({e}); uso i cambi già salvati", "warning")
        for cur, series in RATE_SERIES.items():
            try:
                df = fred_series(series)
                df = df[df["date"] >= "2013-01-01"]
                self.con.executemany("INSERT OR REPLACE INTO macro (series, date, value, source) VALUES (?,?,?,?)",
                                     df[["series", "date", "value", "source"]].itertuples(index=False, name=None))
            except Exception as e:  # noqa: BLE001
                self.say(f"   FRED {series} non disponibile: {e}", "warning")
        self.con.commit()
        self.fx = self._load_fx()

    def _load_fx(self) -> FxTable:
        df = pd.read_sql_query("SELECT date, currency, per_eur, source FROM fx", self.con)
        return FxTable(df)

    def rates(self) -> dict[str, tuple[float, str]]:
        out = {}
        for cur, series in RATE_SERIES.items():
            r = self.con.execute("SELECT date, value, source FROM macro WHERE series=? ORDER BY date DESC LIMIT 1",
                                 (series,)).fetchone()
            if r and r["value"] is not None:
                out[cur] = (float(r["value"]) / 100, f"{r['source']} al {r['date']}")
        return out

    def stage_universe(self) -> None:
        self.say("2/9 Costruzione dell'universo investibile…", stage="universe")
        self.prices = PriceStore(self.con, progress=self.say)
        cands, report = build_universe(self.fx, self.mode, self.prices, progress=self.say,
                                       extra_tickers=self.extra, limit=self.limit)
        rows = candidate_rows(cands)
        # companies not in this run's candidate list are no longer in the universe; attributes learned
        # in earlier runs (sic, tier, filer type...) are kept for the others (merge, not replace)
        self.con.execute("UPDATE companies SET in_universe=0")
        upsert_merge(self.con, "companies", "company_id", rows)
        self.con.commit()
        self.universe = [c for c in cands if not c.exclusion]
        for c in cands:
            if c.info:
                self.infos[c.company_id] = c.info
                self.prices.set_currency(c.ticker, c.info.get("currency"))
        self.con.commit()
        self.stats["universe"] = report
        for s in report["steps"]:
            self.say("   " + s)
        if not self.universe:
            raise RuntimeError("Universo vuoto: controlla la connessione internet e config.toml")

    def stage_prices(self) -> None:
        self.say(f"3/9 Storico prezzi (10+ anni) per {len(self.universe)} titoli + benchmark…", stage="prices")
        tickers = [c.ticker for c in self.universe] + [b for b, _ in BENCHMARKS]
        self.prices.get_full(tickers, read=False)
        # benchmark in EUR
        self.bench_eur, self.bench_name = None, None
        for t, name in BENCHMARKS:
            df = self.prices._read([t], "2014-01-01").get(t)
            if df is None or df.empty or (pd.Timestamp.today() - df.index.max()).days > 10:
                continue
            info = yahoo.info(t) or {}
            cur, div = yahoo.normalize_currency(info.get("currency") or ("EUR" if t.endswith((".MI", ".AS")) else "USD"))
            s = df["adj_close"] / div
            s = self.fx.series_to_eur(s, cur)
            if s is not None and s.dropna().size > 500:
                self.bench_eur, self.bench_name = s.dropna(), name
                break
        self.say(f"   benchmark: {self.bench_name or 'non disponibile'}")

    def stage_fundamentals(self) -> None:
        n = len(self.universe)
        self.say(f"4/9 Bilanci: SEC XBRL per le società registrate alla SEC, Yahoo per le altre ({n} società)…",
                 stage="fundamentals")
        sec_c = [c for c in self.universe if c.cik]
        other = [c for c in self.universe if not c.cik]
        ok_a = ok_b = fail = 0

        def fetch(c):
            try:
                sub = sec.submissions(c.cik)
            except Exception as e:  # noqa: BLE001
                return c, None, None, None, e
            try:
                cf, fetched = sec.companyfacts(c.cik)
            except SourceUnavailable as e:
                if "404" not in str(e):
                    return c, None, None, None, e
                cf, fetched = {"facts": {}}, None      # no XBRL financials (new IPO, some 40-F) → Yahoo fallback
            except Exception as e:  # noqa: BLE001
                return c, None, None, None, e
            return c, sub, cf, fetched, None

        consecutive = 0
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, (c, sub, cf, fetched, err) in enumerate(ex.map(fetch, sec_c)):
                if i and i % 100 == 0:
                    self.say(f"   SEC {i}/{len(sec_c)}")
                if err is not None and sub is None:
                    msg = str(err)
                    if isinstance(err, SourceUnavailable) and ("user_agent" in msg or "User-Agent" in msg):
                        raise err
                    consecutive += 1
                    if consecutive >= 15:
                        # SEC down or throttling: stop instead of silently excluding hundreds of companies
                        raise RuntimeError(f"SEC non raggiungibile ({consecutive} errori consecutivi, ultimo: {msg}). "
                                           "Analisi interrotta: riprova più tardi (i dati già scaricati restano in cache).")
                    self._exclude(c.company_id, f"dati SEC non scaricabili: {err}")
                    fail += 1
                    continue
                consecutive = 0
                try:
                    if self._process_sec(c, sub, cf, fetched):
                        ok_a += 1
                    else:
                        fail += 1
                except Exception as e:  # noqa: BLE001
                    self.say(f"   {c.ticker}: errore normalizzazione SEC: {e}", "warning")
                    self._exclude(c.company_id, f"errore elaborazione dati SEC: {e}")
                    fail += 1
                self.con.commit()
        for i, c in enumerate(other):
            if i and i % 50 == 0:
                self.say(f"   Yahoo {i}/{len(other)}")
            try:
                if self._process_yahoo(c):
                    ok_b += 1
                else:
                    fail += 1
            except Exception as e:  # noqa: BLE001
                self.say(f"   {c.ticker}: errore dati Yahoo: {e}", "warning")
                self._exclude(c.company_id, f"errore dati Yahoo: {e}")
                fail += 1
            self.con.commit()
        self.stats["fundamentals"] = {"tier_A_sec": ok_a, "tier_B_yahoo": ok_b, "failed_or_excluded": fail}
        self.say(f"   bilanci ok: {ok_a} da SEC, {ok_b} da Yahoo; esclusi/falliti: {fail}")

    def _exclude(self, company_id: str, reason: str) -> None:
        self.con.execute("UPDATE companies SET in_universe=0, exclusion_reason=? WHERE company_id=?", (reason, company_id))

    def _process_sec(self, c, sub, cf, fetched) -> bool:
        sic = str(sub.get("sic") or "")
        info = self.infos.get(c.company_id, {})
        if sic == "6770":
            self._exclude(c.company_id, "SPAC / società veicolo (SIC 6770)")
            return False
        forms = [f.get("form") for f in sec.recent_filings(sub)]
        annual_forms = [f for f in forms if f in sec.ANNUAL_FORMS]
        filer = "foreign" if annual_forms and annual_forms[0] in sec.FOREIGN_ANNUAL else "domestic"
        if not annual_forms:
            filer = "domestic"
        splits = self.prices.splits(c.ticker) if filer == "domestic" else []
        fin = normalize_companyfacts(c.company_id, cf, splits=splits, apply_splits=(filer == "domestic"),
                                     fetched_at=datetime.fromtimestamp(fetched, timezone.utc).isoformat() if fetched else None)
        tier = "A"
        if fin.annual.empty:
            # e.g. 40-F filers without XBRL financials → try Yahoo
            st = yahoo.statements(c.ticker)
            if st:
                fin = normalize_yahoo(c.company_id, c.ticker, st, info.get("financialCurrency"))
                tier = "B"
                fin.notes.append("Nessun bilancio XBRL alla SEC: uso i dati Yahoo.")
        if fin.annual.empty:
            self._exclude(c.company_id, "nessun bilancio annuale disponibile (SEC né Yahoo)")
            return False
        rows, flags = qualitative.filings_flags(c.company_id, c.cik, sub)
        upsert(self.con, "filings", rows)
        fin.flags.extend(flags)
        sector = info.get("sector") or classify.sector_from_sic(sic)
        industry = info.get("industry") or sub.get("sicDescription")
        la = None
        if fin.latest_instant("total_liabilities") and fin.latest_instant("total_assets"):
            la = fin.latest_instant("total_liabilities") / fin.latest_instant("total_assets")
        self.con.execute(
            "UPDATE companies SET sic=?, sic_description=?, filer_type=?, data_tier=?, fin_currency=?, sector=?, "
            "industry=?, is_banklike=?, is_reit=?, name=COALESCE(name, ?) WHERE company_id=?",
            (sic, sub.get("sicDescription"), filer, tier, fin.currency, sector, industry,
             int(classify.is_banklike(sic, info.get("industry"), la)), int(classify.is_reit(sic, info.get("industry"))),
             sub.get("name"), c.company_id))
        self._store_facts(fin)
        self.fins[c.company_id] = fin
        return True

    def _process_yahoo(self, c) -> bool:
        info = self.infos.get(c.company_id, {})
        st = yahoo.statements(c.ticker, ttl_days=float(self.cfg.get("cache.fundamentals_ttl_days", 7)))
        if not st:
            self._exclude(c.company_id, "bilanci non disponibili su Yahoo")
            return False
        fcur = info.get("financialCurrency") or yahoo.normalize_currency(info.get("currency"))[0]
        fin = normalize_yahoo(c.company_id, c.ticker, st, fcur, fetched_at=now_iso())
        if fin.annual.empty:
            self._exclude(c.company_id, "bilanci Yahoo vuoti")
            return False
        la = None
        if fin.latest_instant("total_liabilities") and fin.latest_instant("total_assets"):
            la = fin.latest_instant("total_liabilities") / fin.latest_instant("total_assets")
        self.con.execute(
            "UPDATE companies SET filer_type='non_sec', data_tier='B', fin_currency=?, sector=COALESCE(sector,'Unknown'), "
            "is_banklike=?, is_reit=? WHERE company_id=?",
            (fcur, int(classify.is_banklike(None, info.get("industry"), la)),
             int(classify.is_reit(None, info.get("industry"))), c.company_id))
        self._store_facts(fin)
        self.fins[c.company_id] = fin
        return True

    def _store_facts(self, fin: Financials) -> None:
        self.con.execute("DELETE FROM facts WHERE company_id=?", (fin.company_id,))
        if fin.fact_rows:
            # de-duplicate on primary key keeping the last occurrence
            dedup = {}
            for r in fin.fact_rows:
                dedup[(r["item"], r["period_type"], r["period_end"])] = r
            upsert(self.con, "facts", dedup.values())

    def stage_fx_extra(self) -> None:
        need = set()
        for r in self.con.execute("SELECT DISTINCT fin_currency AS c FROM companies WHERE in_universe=1 "
                                  "UNION SELECT DISTINCT price_currency FROM companies WHERE in_universe=1"):
            if r["c"]:
                need.add(r["c"])
        missing = [c for c in need if not self.fx.has(c)]
        if not missing:
            return
        self.say(f"5/9 Cambi non pubblicati dalla BCE da Yahoo: {', '.join(sorted(missing))}", stage="fx")
        for cur in missing:
            s = yahoo.fx_history(f"EUR{cur}=X")
            if s.empty:
                self.say(f"   cambio EUR/{cur} non disponibile: le società con questa valuta non avranno valutazione", "warning")
                continue
            rows = [(d.strftime("%Y-%m-%d"), cur, float(v), f"Yahoo Finance EUR{cur}=X") for d, v in s.items() if v and v > 0]
            self.con.executemany("INSERT OR REPLACE INTO fx (date, currency, per_eur, source) VALUES (?,?,?,?)", rows)
        self.con.commit()
        self.fx = self._load_fx()

    def stage_analysis(self) -> None:
        self.say(f"6/9 Calcolo metriche, valutazione e rischio per {len(self.fins)} società…", stage="analysis")
        comp = {r["company_id"]: dict(r) for r in self.con.execute("SELECT * FROM companies WHERE in_universe=1")}
        rates = self.rates()
        self.analyses = {}
        self.weekly_eur: dict[str, pd.Series] = {}
        meta = {r["ticker"]: r for r in self.con.execute("SELECT * FROM price_meta")}
        tickers = [comp[cid]["ticker"] for cid in self.fins if cid in comp]
        px = self.prices._read(tickers, "2014-01-01")
        metric_rows, flag_rows, conflict_rows = [], [], []
        for cid, fin in self.fins.items():
            c = comp.get(cid)
            if not c:
                continue
            t = c["ticker"]
            df = px.get(t)
            divisor = (meta.get(t)["divisor"] if meta.get(t) else None) or yahoo.normalize_currency(c.get("price_currency"))[1]
            if df is not None:
                df = main_unit(df, divisor)
            info = self.infos.get(cid, {})
            price = float(df["close"].dropna().iloc[-1]) if df is not None and df["close"].notna().any() else None
            try:
                a = analyze_company(c, fin, info, price, df["close"] if df is not None else None,
                                    df["adj_close"] if df is not None else None, self.fx, self.bench_eur, rates, self.cfg)
            except Exception as e:  # noqa: BLE001
                self.say(f"   {t}: errore analisi: {e}", "warning")
                continue
            self.analyses[cid] = a
            if df is not None:
                eur = self.fx.series_to_eur(df["adj_close"], c.get("price_currency") or "USD")
                if eur is not None:
                    self.weekly_eur[cid] = weekly_returns(eur)
            for k, mv in a.metrics.items():
                metric_rows.append({"run_id": self.run_id, "company_id": cid, "metric": k, "value": mv.value, "kind": mv.kind,
                                    "period": mv.period, "method": mv.method, "inputs": json.dumps(mv.inputs, default=str)})
            for f in a.flags:
                flag_rows.append({"run_id": self.run_id, "company_id": cid, "code": f["code"], "severity": f["severity"],
                                  "message": f.get("message"), "evidence": json.dumps(f.get("evidence"), default=str),
                                  "source": f.get("source")})
            for cf in a.conflicts:
                conflict_rows.append({"run_id": self.run_id, "company_id": cid, **cf})
            # store market details & data notes
            md = dict(a.market)
            md["notes"] = fin.notes
            md["ttm_derivation"] = fin.ttm_derivation
            md["ttm_end"] = str(fin.ttm_end.date()) if fin.ttm_end is not None else None
            md["rdcf"] = a.rdcf.as_dict() if a.rdcf else None
            md["hist_multiples"] = (a.hist_multiples.reset_index().assign(date=lambda d: d["date"].astype(str))
                                    .to_dict("records") if a.hist_multiples is not None and not a.hist_multiples.empty else [])
            self.con.execute(
                "INSERT OR REPLACE INTO market_data (company_id, ticker, as_of, price, price_currency, market_cap_yahoo, "
                "shares_yahoo, forward_eps, trailing_eps_yahoo, dividend_rate, beta_yahoo, peg_yahoo, quote_type, raw_json, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, t, str(df.index.max().date()) if df is not None else None, price, c.get("price_currency"),
                 info.get("marketCap"), info.get("impliedSharesOutstanding") or info.get("sharesOutstanding"),
                 info.get("forwardEps"), info.get("trailingEps"), info.get("dividendRate"), info.get("beta"),
                 info.get("trailingPegRatio") or info.get("pegRatio"), info.get("quoteType"),
                 json.dumps(md, default=str), now_iso()))
        upsert(self.con, "metrics", metric_rows)
        upsert(self.con, "flags", flag_rows)
        upsert(self.con, "conflicts", conflict_rows)
        self.con.commit()
        self.say(f"   analizzate {len(self.analyses)} società; {len(flag_rows)} segnalazioni; {len(conflict_rows)} discrepanze tra fonti")

    # ------------------------------------------------------------------ scoring
    def _scoring_frame(self) -> pd.DataFrame:
        comp = pd.read_sql_query("SELECT * FROM companies WHERE in_universe=1", self.con)
        comp = comp[comp["company_id"].isin(self.analyses.keys())]
        vals = {cid: {k: mv.value for k, mv in a.metrics.items()} for cid, a in self.analyses.items()}
        mdf = pd.DataFrame.from_dict(vals, orient="index")
        df = comp.merge(mdf, left_on="company_id", right_index=True, how="left")
        # market cap in EUR (for size tilts / display)
        mc_eur = []
        for _, r in df.iterrows():
            mc_eur.append(self.fx.convert(r.get("market_cap"), r.get("fin_currency") or "USD", "EUR")
                          if pd.notna(r.get("market_cap")) else None)
        df["market_cap_eur"] = mc_eur
        if "momentum_12_1" in df.columns:
            df["momentum_pct"] = pd.to_numeric(df["momentum_12_1"], errors="coerce").rank(pct=True) * 100
        return df

    def stage_scoring(self) -> None:
        self.say("7/9 Punteggi relativi al settore, classificazione e tesi…", stage="scoring")
        df = self._scoring_frame()
        # data gates: no market cap or very stale fundamentals → not scored
        age = df["data_age_days"] if "data_age_days" in df.columns else pd.Series(np.nan, index=df.index)
        stale = pd.to_numeric(age, errors="coerce") > 550
        nomc = df["market_cap"].isna() if "market_cap" in df.columns else pd.Series(True, index=df.index)
        weights = dict(self.cfg.get("scoring.weights", {}))
        scored = scoring.score_universe(df, weights, int(self.cfg.get("scoring.min_peer_group", 8)))
        for col in ("composite", "robust_score", "quality", "valuation", "growth", "financial_strength", "capital_allocation"):
            scored.loc[stale | nomc, col] = np.nan
        scored["robust_percentile"] = scored["robust_score"].rank(pct=True) * 100
        scored["robust_rank"] = scored["robust_score"].rank(ascending=False, method="min")
        for col in ("classification", "classification_reason", "confidence"):
            scored[col] = pd.Series([None] * len(scored), index=scored.index, dtype=object)
        self.scored = scored
        self.deep_done: set[str] = set()
        self._finalize_scores(scored.index)
        self.stats["scored"] = int(scored["robust_score"].notna().sum())
        self.stats["classes"] = scored["classification"].value_counts().to_dict()
        self.say(f"   società con punteggio completo: {self.stats['scored']} / {len(scored)}")

    def _flags_for(self, cid: str) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT code, severity, message, source FROM flags WHERE run_id=? AND company_id=?", (self.run_id, cid))]

    def _finalize_scores(self, idxs) -> None:
        scored = self.scored
        rates = self.rates()
        peer_cols = ["pe", "pb", "ev_ebit", "fcf_sbc_yield", "ev_sales", "dividend_yield", "earnings_yield_equity",
                     "roic_5y_median", "gross_margin", "operating_margin", "net_debt_ebitda", "revenue_cagr_5y"]
        medians = {}
        for g, sub in scored.groupby("peer_used"):
            medians[g] = {c: float(pd.to_numeric(sub[c], errors="coerce").median())
                          for c in peer_cols if c in sub.columns and pd.to_numeric(sub[c], errors="coerce").notna().sum() >= 3}
        rows = []
        for idx in idxs:
            r = scored.loc[idx]
            cid = r["company_id"]
            flags = self._flags_for(cid)
            cls, why = scoring.classify_row(r, flags)
            scored.at[idx, "classification"] = cls
            scored.at[idx, "classification_reason"] = why
            fin_cur = r.get("fin_currency")
            rf = rates.get(fin_cur, (None, ""))[0]
            verdict, vconf, signals = scoring.verdict_for(r, rf, flags)
            if pd.isna(r.get("robust_score")):
                verdict, vconf, signals = "non determinabile", "nessuna", []
            conf = scoring.confidence_of(r, flags) if pd.notna(r.get("robust_score")) else "bassa"
            scored.at[idx, "confidence"] = conf
            a = self.analyses.get(cid)
            metrics = {k: mv.value for k, mv in a.metrics.items()} if a else {}
            r2 = scored.loc[idx]
            thesis = build_thesis(r2, r2["detail_obj"], metrics, flags, medians.get(r2["peer_used"], {}),
                                  a.rdcf.as_dict() if a and a.rdcf else None, fin_cur)
            detail = {"percentiles": r2["detail_obj"], "signals": [s.__dict__ for s in signals], "thesis": thesis,
                      "deep_analysis": cid in self.deep_done,
                      "valuation_peers": _nz(r2.get("valuation_peers")),
                      "peer_medians": medians.get(r2["peer_used"], {}),
                      "scheme_scores": {k.replace("score_", ""): (None if pd.isna(r2[k]) else float(r2[k]))
                                        for k in r2.index if str(k).startswith("score_")}}
            rows.append({
                "run_id": self.run_id, "company_id": cid,
                **{p: _nz(r2.get(p)) for p in ("quality", "growth", "financial_strength", "valuation", "capital_allocation",
                                              "composite", "robust_score", "rank_spread", "coverage")},
                "robust_rank": None if pd.isna(r2.get("robust_rank")) else int(r2["robust_rank"]),
                "confidence": conf, "classification": cls, "valuation_verdict": verdict,
                "valuation_confidence": vconf, "peer_group": r2["peer_used"],
                "detail": json.dumps(detail, default=_json_default),
            })
        upsert(self.con, "scores", rows)
        self.con.commit()

    # ------------------------------------------------------------------ deep analysis
    def stage_deep(self) -> None:
        n = int(self.cfg.get("scoring.deep_analysis_top_n", 150))
        cap = int(self.cfg.get("scoring.deep_analysis_max", 400))
        min_pct = float(self.cfg.get("portfolio.min_robust_percentile", 70))
        s = self.scored.sort_values("robust_score", ascending=False)
        s = s[s["robust_score"].notna()]
        top = list(s["company_id"].iloc[:n])
        # every company that could enter the proposed portfolio must be checked (up to `cap`)
        eligible = [cid for cid, p in zip(s["company_id"], s["robust_percentile"]) if p >= min_pct and cid not in top]
        top += eligible[: max(0, cap - len(top))]
        forced = set(self.extra)
        top += [cid for cid, t in zip(self.scored["company_id"], self.scored["ticker"]) if t in forced and cid not in top]
        self.say(f"8/9 Analisi approfondita dei filing per {len(top)} società (report annuali, eventi, confronto fonti)…",
                 stage="deep")
        comp = {r["company_id"]: dict(r) for r in self.con.execute("SELECT * FROM companies WHERE in_universe=1")}
        n_flags = n_conf = 0
        for i, cid in enumerate(top):
            if i and i % 25 == 0:
                self.say(f"   {i}/{len(top)}")
            c = comp.get(cid)
            if not c:
                continue
            new_flags = []
            payload: dict[str, Any] = {}
            if c.get("cik"):
                try:
                    sub = sec.submissions(c["cik"])
                    payload = qualitative.analyze_annual_reports(c["cik"], sub)
                    new_flags += payload.get("flags", [])
                except Exception as e:  # noqa: BLE001
                    payload = {"notes": [f"analisi testo non riuscita: {e}"]}
                # cross-source check (SEC vs Yahoo) on latest FY
                fin = self.fins.get(cid)
                if fin is not None and fin.tier == "A":
                    n_conf += self._cross_check(cid, c, fin)
            else:
                payload = {"notes": ["Società non registrata alla SEC: analisi testuale dei report non disponibile "
                                     "(i report annuali europei/asiatici non hanno un'API gratuita standard)."]}
            payload.setdefault("performed", bool(c.get("cik")))
            payload["run_id"] = self.run_id
            if payload.get("performed"):
                self.deep_done.add(cid)
            self.con.execute("INSERT OR REPLACE INTO qualitative (company_id, as_of, payload) VALUES (?,?,?)",
                             (cid, now_iso(), json.dumps(payload, default=str)))
            for f in new_flags:
                self.con.execute("INSERT INTO flags (run_id, company_id, code, severity, message, evidence, source) "
                                 "VALUES (?,?,?,?,?,?,?)",
                                 (self.run_id, cid, f["code"], f["severity"], f.get("message"),
                                  json.dumps(f.get("evidence"), default=str), f.get("source")))
                n_flags += 1
            self.con.commit()
        idxs = self.scored.index[self.scored["company_id"].isin(top)]
        self._finalize_scores(idxs)
        self.stats["deep"] = {"companies": len(top), "text_flags": n_flags, "source_conflicts": n_conf}
        self.say(f"   {n_flags} segnalazioni dal testo dei report, {n_conf} discrepanze SEC vs Yahoo")

    def _cross_check(self, cid: str, c: dict, fin: Financials) -> int:
        st = yahoo.statements(c["ticker"])
        if not st:
            return 0
        yfin = normalize_yahoo(cid, c["ticker"], st, fin.currency)
        n = 0
        for item in ("revenue", "net_income", "total_assets", "ocf"):
            a_s, b_s = fin.series(item), yfin.series(item)
            if a_s.empty or b_s.empty:
                continue
            end = a_s.index[-1]
            close = [d for d in b_s.index if abs((d - end).days) <= 20]
            if not close:
                continue
            va, vb = float(a_s.iloc[-1]), float(b_s.loc[close[0]])
            if va == 0:
                continue
            diff = vb / va - 1
            if abs(diff) > 0.02:
                reason = {"net_income": "possibile differenza di definizione (utile di pertinenza del gruppo vs totale, "
                                        "attività cessate) o riclassificazione",
                          "revenue": "possibile differenza di definizione dei ricavi (es. inclusione di altri proventi) o riclassificazione",
                          "total_assets": "possibile riclassificazione o dato riferito a data diversa",
                          "ocf": "possibile differenza di classificazione dei flussi (continuing vs totale)"}[item]
                self.con.execute("INSERT INTO conflicts (run_id, company_id, item, period_end, value_a, source_a, value_b, "
                                 "source_b, pct_diff, likely_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (self.run_id, cid, item, str(end.date()), va, "SEC XBRL (usato)", vb, "Yahoo Finance",
                                  diff, reason))
                n += 1
        return n

    # ------------------------------------------------------------------ portfolio
    def stage_portfolio(self) -> None:
        self.say("9/9 Costruzione del portafoglio proposto…", stage="portfolio")
        weekly = pd.DataFrame(self.weekly_eur)
        sc = self.scored.copy()
        sc["deep_analysis"] = sc["company_id"].isin(getattr(self, "deep_done", set()))
        codes: dict[str, set] = {}
        for r in self.con.execute("SELECT company_id, code FROM flags WHERE run_id=?", (self.run_id,)):
            codes.setdefault(r["company_id"], set()).add(r["code"])
        sc["flag_codes"] = sc["company_id"].map(lambda c: codes.get(c, set()))
        # previous proposal (same mode, completed run) → turnover control
        previous: dict[str, float] = {}
        prev = self.con.execute(
            "SELECT p.payload FROM portfolios p JOIN runs r ON r.run_id=p.run_id WHERE r.status='completed' AND r.mode=? "
            "AND p.name='proposto' AND p.run_id < ? ORDER BY p.run_id DESC LIMIT 1", (self.mode, self.run_id)).fetchone()
        if prev:
            try:
                pp = json.loads(prev["payload"])
                if pp.get("status", "proposto") == "proposto":
                    previous = {p["company_id"]: float(p["weight"]) for p in pp.get("positions", [])}
            except (ValueError, KeyError, TypeError):
                previous = {}
        port = construct_portfolio(sc, weekly, self.cfg, previous)
        bench_w = weekly_returns(self.bench_eur) if self.bench_eur is not None else None
        if port["positions"]:
            w = {p["company_id"]: p["weight"] for p in port["positions"]}
            meta = sc[["company_id", "sector", "region", "price_currency", "quality", "valuation", "growth",
                       "financial_strength", "momentum_pct", "market_cap_eur"]]
            an = analyze_portfolio(w, weekly, meta, bench_w)
            port["analytics"] = serializable(an)
        port["benchmark"] = self.bench_name
        port["method"] = ("Selezione greedy con penalità di correlazione e vincoli di settore/area; pesi 50% uguali + 50% "
                          "inversi alla volatilità, inclinati del ±30% in base al punteggio, poi i pesi più vicini che "
                          "rispettano i limiti per titolo, settore e area. Non è un'ottimizzazione media-varianza: la "
                          "matrice di covarianza serve solo a misurare il rischio.")
        upsert(self.con, "portfolios", [{"run_id": self.run_id, "name": "proposto", "payload": json.dumps(port, default=_json_default)}])
        self.con.commit()
        self.stats["portfolio_positions"] = len(port["positions"])
        for line in port["log"]:
            self.say("   " + line)
        self.say(f"   portafoglio: {len(port['positions'])} posizioni")

    # ------------------------------------------------------------------ diff
    def stage_diff(self) -> None:
        # compare with the previous completed run of the SAME mode (different modes = different universes)
        prev = self.con.execute("SELECT run_id FROM runs WHERE status='completed' AND mode=? AND run_id < ? "
                                "ORDER BY run_id DESC LIMIT 1", (self.mode, self.run_id)).fetchone()
        if not prev:
            self.stats["diff"] = {"note": f"prima analisi in modalità {self.mode}: nessun confronto disponibile"}
            return
        from .changes import compute_changes

        ch = compute_changes(self.con, self.run_id, prev["run_id"])
        self.stats["diff"] = {"previous_run": prev["run_id"], "changes": int(len(ch)),
                              "high": int((ch["gravità"] == "alta").sum()) if len(ch) else 0}
        self.say(f"   cambiamenti rispetto all'analisi #{prev['run_id']}: {len(ch)} "
                 f"({self.stats['diff']['high']} di gravità alta)")

    def _prune(self, keep: int = 24) -> None:
        old = [r["run_id"] for r in self.con.execute(
            "SELECT run_id FROM runs WHERE status='completed' ORDER BY run_id DESC LIMIT -1 OFFSET ?", (keep,))]
        for rid in old:
            for t in ("metrics", "scores", "flags", "conflicts", "portfolios"):
                self.con.execute(f"DELETE FROM {t} WHERE run_id=?", (rid,))
        self.con.commit()


def _nz(v):
    try:
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
    except (TypeError, ValueError):
        return None


def _json_default(o):
    if isinstance(o, (np.floating, np.integer)):
        v = float(o)
        return None if math.isnan(v) else v
    if isinstance(o, (pd.Timestamp, datetime)):
        return str(o)
    if isinstance(o, float) and math.isnan(o):
        return None
    return str(o)
