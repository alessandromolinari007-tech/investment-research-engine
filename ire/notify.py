"""Telegram alerts after every analysis.

Privacy: the bot token and the chat id live ONLY in `config.local.toml` (personal file, excluded from git).
The token is part of every Telegram URL, so it is removed from any error text before it is logged or printed.

Nothing here places orders or touches a broker: it only tells the user what changed.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable

import pandas as pd
import requests

from .db import log, now_iso
from .glossary import num_it, pct

API = "https://api.telegram.org/bot{token}/{method}"
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,60}$")
MAX_CHARS = 3500                     # Telegram limit is 4096
MAX_LINES_PER_SECTION = 10


# ---------------------------------------------------------------------------------------------------------------
# Telegram transport
# ---------------------------------------------------------------------------------------------------------------
def redact(text: Any, token: str | None) -> str:
    s = str(text)
    if token:
        s = s.replace(token, "***")
    return re.sub(r"bot\d{5,15}:[A-Za-z0-9_-]{20,}", "bot***", s)


def credentials(cfg) -> tuple[str, str]:
    return str(cfg.get("telegram.bot_token", "") or "").strip(), str(cfg.get("telegram.chat_id", "") or "").strip()


def telegram_configured(cfg) -> bool:
    token, chat = credentials(cfg)
    return bool(TOKEN_RE.match(token)) and bool(chat)


def _call(token: str, method: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    try:
        r = requests.post(API.format(token=token, method=method), json=payload or {}, timeout=timeout)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(redact(f"connessione a Telegram non riuscita: {type(e).__name__}", token)) from None
    if not data.get("ok"):
        raise RuntimeError(redact(f"Telegram ha risposto: {data.get('description', r.status_code)}", token))
    return data


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Splits at line boundaries (a single over-long line is cut)."""
    parts, cur = [], ""
    for line in text.splitlines():
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return [p.rstrip("\n") for p in parts]


def send_telegram(text: str, cfg, post: Callable[..., dict[str, Any]] | None = None) -> tuple[bool, str]:
    """(ok, error text without secrets). Plain text, no formatting: nothing in a company name can break it."""
    post = post or _call                                  # looked up now (not at import): replaceable in tests
    token, chat = credentials(cfg)
    if not telegram_configured(cfg):
        return False, "Telegram non configurato (esegui: python -m ire telegram)"
    try:
        for part in split_message(text):
            post(token, "sendMessage", {"chat_id": chat, "text": part, "disable_web_page_preview": True})
    except RuntimeError as e:
        return False, redact(e, token)
    return True, ""


def get_me(token: str) -> str:
    """Bot username (validates the token)."""
    return str(_call(token, "getMe")["result"].get("username", ""))


def detect_chat_id(token: str) -> str | None:
    """Chat id of the most recent PRIVATE message sent to the bot (the user pressed Start)."""
    res = _call(token, "getUpdates")["result"]
    for upd in reversed(res):
        chat = (upd.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") is not None:
            return str(chat["id"])
    return None


# ---------------------------------------------------------------------------------------------------------------
# what to say
# ---------------------------------------------------------------------------------------------------------------
def _followed(con: sqlite3.Connection, portfolio: dict[str, Any] | None) -> dict[str, str]:
    """company_id → why the user cares: own holdings, watchlist, current proposed portfolio."""
    out: dict[str, str] = {}
    tick = {r["ticker"]: r["company_id"] for r in con.execute("SELECT ticker, company_id FROM companies")}
    for r in con.execute("SELECT ticker FROM user_portfolio WHERE weight > 0"):
        if r["ticker"] in tick:
            out[tick[r["ticker"]]] = "tuo titolo"
    for r in con.execute("SELECT ticker FROM watchlist"):
        if r["ticker"] in tick:
            out.setdefault(tick[r["ticker"]], "in osservazione")
    for p in (portfolio or {}).get("positions", []):
        out.setdefault(p["company_id"], "nel portafoglio proposto")
    return out


def _portfolio(con: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    r = con.execute("SELECT payload FROM portfolios WHERE run_id=? AND name='proposto'", (run_id,)).fetchone()
    return json.loads(r["payload"]) if r else None


def build_messages(con: sqlite3.Connection, run_id: int, cfg) -> dict[str, str]:
    """{kind: text} for the alerts enabled in [alerts]; empty if there is nothing worth saying."""
    run = con.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return {}
    try:
        summ = json.loads(run["summary"] or "{}")
    except ValueError:
        summ = {}
    out: dict[str, str] = {}
    status = run["status"]
    if status in ("failed", "degraded"):
        if cfg.get("alerts.on_problem", True):
            if status == "failed":
                why = str(summ.get("error", "motivo sconosciuto")).splitlines()[0][:300]
                out["problem"] = (f"Analisi #{run_id} FALLITA ({run['mode']}).\nMotivo: {why}\n"
                                  "I risultati precedenti restano validi. Riprova più tardi o controlla il log "
                                  "(pagina Dati e metodologia).")
            else:
                issues = summ.get("health") or []
                out["problem"] = (f"Analisi #{run_id} INCOMPLETA ({run['mode']}): troppi dati mancanti o non aggiornati.\n"
                                  + "\n".join("- " + str(i)[:200] for i in issues[:5])
                                  + "\nIl sito continua a mostrare l'ultima analisi completa.")
        return out
    if status != "completed":
        return out
    port = _portfolio(con, run_id)
    diff = summ.get("diff") or {}
    prev = diff.get("previous_run")
    mins = None
    if run["started_at"] and run["finished_at"]:
        mins = (pd.Timestamp(run["finished_at"]) - pd.Timestamp(run["started_at"])).total_seconds() / 60

    if cfg.get("alerts.summary", True):
        lines = [f"Analisi #{run_id} completata ({run['mode']}" + (f", {num_it(mins, 0)} minuti" if mins else "") + ")."]
        fund = summ.get("fundamentals") or {}
        if summ.get("scored") is not None:
            lines.append(f"{summ['scored']} società con punteggio"
                         + (f" su {fund.get('tier_A_sec', 0) + fund.get('tier_B_yahoo', 0)} analizzate." if fund else "."))
        if port:
            lines.append(f"Portafoglio proposto: {len(port.get('positions', []))} titoli ({port.get('status')}).")
        if prev:
            lines.append(f"Cambiamenti rispetto all'analisi #{prev}: {diff.get('changes', 0)} ({diff.get('high', 0)} gravi).")
        else:
            lines.append("È la prima analisi di questo tipo: ancora nessun confronto.")
        out["summary"] = "\n".join(lines)

    if prev and cfg.get("alerts.on_changes", True):
        from .changes import compute_changes

        ch = compute_changes(con, run_id, int(prev))
        follow = _followed(con, port)
        if len(ch):
            ch = ch[ch["company_id"].isin(follow) & ch["gravità"].isin(["alta", "media"])]
        if len(ch):
            lines = ["Cambiamenti importanti sui titoli che segui:"]
            for _, r in ch.head(MAX_LINES_PER_SECTION).iterrows():
                lines.append(f"- {r['ticker']} ({follow[r['company_id']]}) [{r['gravità']}]: {r['cambiamento']}")
            if len(ch) > MAX_LINES_PER_SECTION:
                lines.append(f"... e altri {len(ch) - MAX_LINES_PER_SECTION}.")
            out["changes"] = "\n".join(lines)

    if port and prev and cfg.get("alerts.on_portfolio", True):
        pos = port.get("positions", [])
        new = [p for p in pos if p.get("previous_weight") is None]
        exited = port.get("exited", [])
        if port.get("status") == "proposto" and (new or exited) and port.get("turnover") is not None:
            names = {r["company_id"]: r["ticker"] for r in con.execute("SELECT company_id, ticker FROM companies")}
            lines = [f"Portafoglio proposto: rotazione {pct(port['turnover'], 0)} del capitale."]
            if new:
                lines.append("Entrano: " + ", ".join(p["ticker"] for p in new[:15]))
            if exited:
                lines.append("Escono: " + ", ".join(names.get(c, c) for c in exited[:15]))
            lines.append("È un'indicazione di studio, non un ordine: verifica le schede prima di qualsiasi decisione.")
            out["portfolio"] = "\n".join(lines)
    return out


def notify_after_run(con: sqlite3.Connection, run_id: int, cfg,
                     post: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Sends the enabled alerts of one run (each kind once per run). Never raises: an alert must not break an analysis."""
    result: dict[str, Any] = {"sent": [], "skipped": None, "error": None}
    try:
        if not telegram_configured(cfg):
            result["skipped"] = "Telegram non configurato"
            return result
        done = {r["kind"] for r in con.execute("SELECT kind FROM alerts_sent WHERE run_id=?", (run_id,))}
        msgs = {k: v for k, v in build_messages(con, run_id, cfg).items() if k not in done}
        if not msgs:
            result["skipped"] = "nessun avviso da inviare"
            return result
        # one Telegram message per run: the problem alone, or summary + changes + portfolio together
        order = [k for k in ("problem", "summary", "changes", "portfolio") if k in msgs]
        ok, err = send_telegram("\n\n".join(msgs[k] for k in order), cfg, post=post)
        if ok:
            con.executemany("INSERT OR REPLACE INTO alerts_sent (run_id, kind, sent_at) VALUES (?,?,?)",
                            [(run_id, k, now_iso()) for k in order])
            con.commit()
            result["sent"] = order
            log(con, run_id, "info", "notify", "Avviso Telegram inviato: " + ", ".join(order))
        else:
            result["error"] = err
            log(con, run_id, "warning", "notify", "Avviso Telegram NON inviato: " + err)
    except Exception as e:  # noqa: BLE001
        token = credentials(cfg)[0]
        result["error"] = redact(f"{type(e).__name__}: {e}", token)
        try:
            log(con, run_id, "warning", "notify", "Avviso Telegram NON inviato: " + result["error"])
        except Exception:  # noqa: BLE001
            pass
    return result
