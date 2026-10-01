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


def configure(check_only: bool = False) -> bool:
    """SEC contact (name + email). Written in config.local.toml (personal file, excluded from git) so it is
    kept when config.toml is replaced by a new version of the project. Returns True if configured."""
    from .config import load_config, local_config_path

    cfg = load_config()
    if cfg.sec_user_agent and "@" in cfg.sec_user_agent:
        print("SEC user_agent già configurato.")
        return True
    if check_only:
        print("SEC user_agent NON configurato.")
        return False
    if not sys.stdin or not sys.stdin.isatty():
        print("SEC user_agent non configurato e nessuna console interattiva: esegui setup.bat oppure "
              "`python -m ire configure`, o scrivi a mano [sec] user_agent in config.local.toml.")
        return False
    print("La SEC (fonte ufficiale dei bilanci USA) chiede nome ed email di chi scarica i dati.")
    print("Vengono salvati SOLO sul tuo PC (config.local.toml) e inviati SOLO a sec.gov.")
    name = input("Nome e cognome: ").strip()
    email = input("Email: ").strip()
    if not name or "@" not in email or any(c in name + email for c in "\"\n\r"):
        print("Dati non validi: niente è stato salvato. Riprova, oppure scrivi a mano in config.local.toml:")
        print('  [sec]\n  user_agent = "Nome Cognome email@dominio"')
        return False
    path = local_config_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = [ln for ln in lines if not ln.strip().startswith("user_agent")]
    if "[sec]" not in (ln.strip() for ln in lines):
        lines += ["", "[sec]"]
    i = [ln.strip() for ln in lines].index("[sec]")
    lines.insert(i + 1, f'user_agent = "{name} {email}"')
    if not path.exists():
        lines.insert(0, "# Impostazioni personali: NON vengono pubblicate su GitHub (vedi .gitignore).")
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    ok = "@" in load_config(reload=True).sec_user_agent
    print(f"Salvato in {path.name}." if ok else f"ATTENZIONE: scrittura di {path.name} non riuscita.")
    return ok


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
    cf = sub.add_parser("configure", help="imposta il contatto richiesto dalla SEC")
    cf.add_argument("--check", action="store_true", help="verifica soltanto (codice di uscita 1 se manca)")
    w = sub.add_parser("watch", help="gestisce la watchlist")
    w.add_argument("action", choices=["add", "remove", "list"])
    w.add_argument("tickers", nargs="*")
    a = p.parse_args(argv)
    from .config import ConfigError

    try:
        _dispatch(a)
    except ConfigError as e:
        print(f"ERRORE DI CONFIGURAZIONE: {e}")
        sys.exit(2)


def _dispatch(a) -> None:
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
        import threading
        import webbrowser

        from .config import load_config

        load_config()          # a broken config.toml is reported here, not as a traceback inside the app
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        port = "8501"
        # headless: no first-run "Email:" prompt that would block the window; the browser is opened here
        print(f"Interfaccia in avvio su http://localhost:{port}  (chiudi questa finestra per fermarla)")
        threading.Timer(4.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
        rc = subprocess.run([sys.executable, "-m", "streamlit", "run", os.path.join(here, "app", "app.py"),
                             "--server.headless", "true", "--server.port", port,
                             "--browser.gatherUsageStats", "false"], check=False).returncode
        sys.exit(rc)
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
        sys.exit(0 if configure(check_only=a.check) else 1)
    elif a.cmd == "check":
        from .selfcheck import run_checks

        ok = run_checks()
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
