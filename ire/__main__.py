"""Command line entry point:  python -m ire <command>

  run      [--mode quick|standard|full] [--tickers AAPL,ENI.MI] [--skip-deep] [--limit N]
  status   show the last runs and data coverage
  app      start the web interface (Streamlit)
  watch    add|remove|list TICKER   (watchlist: always analyzed, even below size/liquidity thresholds)
  check    verify configuration and connectivity to every data source
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def configure():
    """Asks for the SEC contact (name + email) and writes it in config.toml."""
    from .config import CONFIG_PATH, load_config

    cfg = load_config()
    if cfg.sec_user_agent and "@" in cfg.sec_user_agent:
        print(f"SEC user_agent già configurato: {cfg.sec_user_agent}")
        return
    print("La SEC (fonte ufficiale dei bilanci USA) chiede nome ed email di chi scarica i dati.")
    print("Restano sul tuo PC e vengono inviati SOLO a sec.gov.")
    name = input("Nome e cognome: ").strip()
    email = input("Email: ").strip()
    if not name or "@" not in email:
        print("Dati non validi: puoi modificarli a mano in config.toml (sezione [sec]).")
        return
    text = CONFIG_PATH.read_text(encoding="utf-8")
    text = text.replace('user_agent = ""', f'user_agent = "{name} {email}"', 1)
    CONFIG_PATH.write_text(text, encoding="utf-8")
    load_config(reload=True)
    print("Salvato in config.toml.")


def main(argv=None):
    _utf8_console()
    p = argparse.ArgumentParser(prog="python -m ire", description="Investment Research Engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="esegue la pipeline completa")
    r.add_argument("--mode", choices=["quick", "standard", "full"])
    r.add_argument("--tickers", default="", help="ticker aggiuntivi separati da virgola (notazione Yahoo)")
    r.add_argument("--skip-deep", action="store_true", help="salta l'analisi del testo dei filing")
    r.add_argument("--limit", type=int, default=None, help="limita il numero di candidati (test)")
    sub.add_parser("status", help="stato delle ultime analisi")
    sub.add_parser("app", help="avvia l'interfaccia")
    sub.add_parser("check", help="verifica configurazione e fonti dati")
    sub.add_parser("configure", help="imposta il contatto richiesto dalla SEC")
    w = sub.add_parser("watch", help="gestisce la watchlist")
    w.add_argument("action", choices=["add", "remove", "list"])
    w.add_argument("tickers", nargs="*")
    a = p.parse_args(argv)

    if a.cmd == "run":
        from .pipeline import Pipeline

        Pipeline(mode=a.mode, extra_tickers=[t for t in a.tickers.split(",") if t], skip_deep=a.skip_deep,
                 limit=a.limit).run()
    elif a.cmd == "status":
        from .db import init_db

        con = init_db()
        for row in con.execute("SELECT run_id, started_at, finished_at, mode, status, summary FROM runs ORDER BY run_id DESC LIMIT 5"):
            summ = json.loads(row["summary"] or "{}")
            print(f"#{row['run_id']} {row['status']:9s} {row['mode']:8s} {row['started_at']} → {row['finished_at']}")
            if summ.get("fundamentals"):
                print("    bilanci:", summ["fundamentals"], "| con punteggio:", summ.get("scored"))
            if summ.get("error"):
                print("    errore:", summ["error"])
    elif a.cmd == "app":
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        subprocess.run([sys.executable, "-m", "streamlit", "run", os.path.join(here, "app", "app.py"),
                        "--browser.gatherUsageStats", "false"], check=False)
    elif a.cmd == "watch":
        from .db import init_db, now_iso

        con = init_db()
        if a.action == "add":
            for t in a.tickers:
                con.execute("INSERT OR REPLACE INTO watchlist (ticker, added_at) VALUES (?,?)", (t.upper(), now_iso()))
        elif a.action == "remove":
            for t in a.tickers:
                con.execute("DELETE FROM watchlist WHERE ticker=?", (t.upper(),))
        con.commit()
        print("Watchlist:", [r[0] for r in con.execute("SELECT ticker FROM watchlist ORDER BY ticker")])
    elif a.cmd == "configure":
        configure()
    elif a.cmd == "check":
        from .selfcheck import run_checks

        ok = run_checks()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
