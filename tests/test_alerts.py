"""Telegram alerts, personal config file, automatic-update guard. No network: the Telegram call is replaced."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ire.notify as N
from ire.config import Config, load_config, set_local_value, validate
from ire.schedule import update_needed

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456789:AAEhBP0av28zKcb8YpWkJ3sQeNfTgUvXyZa"          # fake, right shape
CHAT = "987654321"


def _cfg(**over):
    raw = {"telegram": {"bot_token": TOKEN, "chat_id": CHAT},
           "alerts": {"on_problem": True, "summary": True, "on_changes": True, "on_portfolio": True}}
    for k, v in over.items():
        raw.setdefault(k, {}).update(v)
    return Config(raw=raw)


class Capture:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def __call__(self, token, method, payload=None, timeout=20):
        if self.fail:
            raise RuntimeError(self.fail)
        self.sent.append((method, payload))
        return {"ok": True, "result": {}}


# ------------------------------------------------------------------------------------------------ transport
def test_split_message_respects_limit_and_lines():
    text = "\n".join(f"riga {i} " + "x" * 200 for i in range(60))
    parts = N.split_message(text, 1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts).count("riga") == 60
    assert N.split_message("a" * 5000, 1000) == ["a" * 1000] * 5


def test_send_requires_configuration_and_never_leaks_the_token():
    ok, err = N.send_telegram("ciao", Config(raw={}), post=Capture())
    assert not ok and "non configurato" in err
    cap = Capture(fail=f"errore su https://api.telegram.org/bot{TOKEN}/sendMessage")
    ok, err = N.send_telegram("ciao", _cfg(), post=cap)
    assert not ok and TOKEN not in err and "***" in err
    cap = Capture()
    ok, _ = N.send_telegram("ciao", _cfg(), post=cap)
    assert ok and cap.sent[0][1]["chat_id"] == CHAT and cap.sent[0][1]["text"] == "ciao"


def test_redact_hides_a_token_inside_any_url():
    assert TOKEN not in N.redact(f"HTTPSConnectionPool: /bot{TOKEN}/getMe", None)
    assert TOKEN not in N.redact(f"boom {TOKEN}", TOKEN)


# ------------------------------------------------------------------------------------------------ config
def test_versioned_config_has_no_secrets():
    import tomllib

    c = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    assert c["telegram"]["bot_token"] == "" and c["telegram"]["chat_id"] == ""
    assert c["sec"]["user_agent"] == ""
    assert "config.local.toml" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_validation_rejects_a_bad_token_without_echoing_it():
    errs = validate(_cfg(telegram={"bot_token": "abc-segretissimo"}))
    assert any("bot_token" in e for e in errs) and not any("segretissimo" in e for e in errs)
    assert any("chat_id" in e for e in validate(_cfg(telegram={"chat_id": "ciao"})))
    assert any("on_changes" in e for e in validate(_cfg(alerts={"on_changes": "si"})))
    assert validate(_cfg()) == []


def test_set_local_value_keeps_other_settings(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[sec]\nuser_agent = ""\n', encoding="utf-8")
    local = tmp_path / "config.local.toml"
    local.write_text('# mio\n[sec]\nuser_agent = "Nome Cognome nome@example.com"\n', encoding="utf-8")
    set_local_value("telegram", "bot_token", TOKEN, cfg)
    set_local_value("telegram", "chat_id", CHAT, cfg)
    set_local_value("telegram", "chat_id", "555666777", cfg)                 # replaced, not duplicated
    text = local.read_text(encoding="utf-8")
    assert text.count("chat_id") == 1 and "Nome Cognome nome@example.com" in text and text.startswith("# mio")
    loaded = load_config(cfg)
    assert loaded.get("telegram.bot_token") == TOKEN and loaded.get("telegram.chat_id") == "555666777"
    assert loaded.sec_user_agent.startswith("Nome Cognome")
    with pytest.raises(ValueError):
        set_local_value("telegram", "chat_id", 'a"b', cfg)


# ------------------------------------------------------------------------------------------------ what is said
def _two_runs(world):
    from ire.db import connect
    from ire.pipeline import Pipeline

    r1 = Pipeline(mode="quick", verbose=False).run()
    r2 = Pipeline(mode="quick", verbose=False).run()
    return connect(), r1, r2


def test_summary_changes_and_portfolio_messages(world):
    from ire.scoring import C_QDISC, C_REDFLAG

    con, r1, r2 = _two_runs(world)
    port = json.loads(con.execute("SELECT payload FROM portfolios WHERE run_id=? AND name='proposto'", (r2,)).fetchone()[0])
    cid = port["positions"][0]["company_id"]
    tick = port["positions"][0]["ticker"]
    con.execute("UPDATE scores SET classification=? WHERE run_id=? AND company_id=?", (C_QDISC, r1, cid))
    con.execute("UPDATE scores SET classification=? WHERE run_id=? AND company_id=?", (C_REDFLAG, r2, cid))
    other = next(r[0] for r in con.execute("SELECT company_id FROM scores WHERE run_id=? AND company_id<>?", (r2, cid)))
    con.execute("UPDATE scores SET classification=? WHERE run_id=? AND company_id=?", (C_QDISC, r1, other))
    con.execute("UPDATE scores SET classification=? WHERE run_id=? AND company_id=?", (C_REDFLAG, r2, other))
    con.commit()
    # the second company is neither in the proposed portfolio nor watched: no alert about it
    in_port = {p["company_id"] for p in port["positions"]}
    other_t = con.execute("SELECT ticker FROM companies WHERE company_id=?", (other,)).fetchone()[0]
    msgs = N.build_messages(con, r2, _cfg())
    assert "completata" in msgs["summary"] and f"#{r1}" in msgs["summary"]
    assert tick in msgs["changes"] and "nel portafoglio proposto" in msgs["changes"]
    if other not in in_port:
        assert other_t not in msgs["changes"]
    # watching it makes it relevant
    con.execute("INSERT INTO watchlist (ticker, added_at) VALUES (?, '2026-01-01')", (other_t,))
    con.commit()
    assert other_t in N.build_messages(con, r2, _cfg())["changes"]
    # switches
    off = N.build_messages(con, r2, _cfg(alerts={"summary": False, "on_changes": False, "on_portfolio": False}))
    assert off == {}


def test_failed_and_degraded_runs_are_reported_without_traceback(world, monkeypatch):
    from ire.db import connect
    from ire.pipeline import Pipeline

    cap = Capture()
    monkeypatch.setattr(N, "_call", cap)
    cfg = load_config()
    cfg.raw["telegram"] = {"bot_token": TOKEN, "chat_id": CHAT}

    def boom(self):
        raise RuntimeError("SEC non raggiungibile")

    monkeypatch.setattr(Pipeline, "stage_universe", boom)
    with pytest.raises(RuntimeError):
        Pipeline(mode="quick", verbose=False).run()
    assert len(cap.sent) == 1
    text = cap.sent[0][1]["text"]
    assert "FALLITA" in text and "SEC non raggiungibile" in text and "Traceback" not in text and TOKEN not in text
    n = connect().execute("SELECT COUNT(*) FROM alerts_sent WHERE kind='problem'").fetchone()[0]
    assert n == 1


def test_alert_failure_never_breaks_a_run_and_is_logged_without_token(world, monkeypatch):
    from ire.db import connect
    from ire.pipeline import Pipeline

    monkeypatch.setattr(N, "_call", Capture(fail=f"rete giu' su /bot{TOKEN}/sendMessage"))
    load_config().raw["telegram"] = {"bot_token": TOKEN, "chat_id": CHAT}
    rid = Pipeline(mode="quick", verbose=False).run()
    con = connect()
    assert con.execute("SELECT status FROM runs WHERE run_id=?", (rid,)).fetchone()[0] == "completed"
    msgs = [r[0] for r in con.execute("SELECT message FROM log WHERE run_id=? AND stage='notify'", (rid,))]
    assert msgs and all(TOKEN not in m for m in msgs) and "NON inviato" in msgs[0]


def test_each_alert_is_sent_once_per_run(world):
    con, r1, r2 = _two_runs(world)
    cap = Capture()
    res = N.notify_after_run(con, r2, _cfg(), post=cap)
    assert res["sent"] and len(cap.sent) == 1
    again = N.notify_after_run(con, r2, _cfg(), post=cap)
    assert not again["sent"] and len(cap.sent) == 1
    assert N.notify_after_run(con, r2, Config(raw={}), post=cap)["skipped"] == "Telegram non configurato"


def test_without_telegram_nothing_is_attempted(world, monkeypatch):
    from ire.pipeline import Pipeline

    called = []
    monkeypatch.setattr(N, "_call", lambda *a, **k: called.append(1))
    Pipeline(mode="quick", verbose=False).run()
    assert not called


# ------------------------------------------------------------------------------------------------ automatic updates
def test_update_needed_rules(world):
    from ire.db import init_db

    con = init_db()
    assert update_needed(con, 7, "standard")[:2] == (True, "standard")
    now = datetime.now(timezone.utc)
    con.execute("INSERT INTO runs (started_at, finished_at, mode, status) VALUES (?,?,?,?)",
                ((now - timedelta(days=2, minutes=20)).isoformat(), (now - timedelta(days=2)).isoformat(), "quick", "completed"))
    con.execute("INSERT INTO runs (started_at, finished_at, mode, status) VALUES (?,?,?,?)",
                ((now - timedelta(hours=1)).isoformat(), (now - timedelta(minutes=30)).isoformat(), "standard", "failed"))
    con.commit()
    needed, mode, why = update_needed(con, 7, "standard", now)
    assert not needed and mode == "quick" and "ancora recente" in why        # a failed run does not count
    needed, mode, _ = update_needed(con, 1, "standard", now)
    assert needed and mode == "quick"                                         # same mode as the last good analysis
    needed, _, _ = update_needed(con, 7, "standard", now + timedelta(days=6))
    assert needed


def test_update_command_skips_when_recent(world, capsys, monkeypatch):
    from ire.__main__ import main
    from ire.pipeline import Pipeline

    Pipeline(mode="quick", verbose=False).run()
    started = []
    monkeypatch.setattr(Pipeline, "run", lambda self: started.append(1))
    main(["update"])
    assert not started and "ancora recente" in capsys.readouterr().out


def test_guided_telegram_setup(world, monkeypatch, capsys):
    import builtins
    import sys

    import ire.config as C
    from ire.__main__ import configure_telegram

    calls = []

    def fake(token, method, payload=None, timeout=20):
        calls.append(method)
        if method == "getMe":
            return {"ok": True, "result": {"username": "mio_ire_bot"}}
        if method == "getUpdates":
            return {"ok": True, "result": [{"message": {"chat": {"id": -1001, "type": "group"}}},
                                           {"message": {"chat": {"id": 424242, "type": "private"}}}]}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(N, "_call", fake)
    answers = iter([TOKEN, ""])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": lambda self: True})())
    assert configure_telegram() is True
    assert calls == ["getMe", "getUpdates", "sendMessage"]
    text = C.local_config_path().read_text(encoding="utf-8")
    assert TOKEN in text and 'chat_id = "424242"' in text                      # private chat, not the group
    out = capsys.readouterr().out
    assert TOKEN not in out and "mio_ire_bot" in out
    assert N.telegram_configured(C.load_config(reload=True))


def test_guided_setup_rejects_a_malformed_token_and_saves_nothing(world, monkeypatch):
    import builtins
    import sys

    import ire.config as C
    from ire.__main__ import configure_telegram

    monkeypatch.setattr(builtins, "input", lambda prompt="": "non-un-token")
    monkeypatch.setattr(sys, "stdin", type("T", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(N, "_call", lambda *a, **k: pytest.fail("nessuna richiesta di rete con un token malformato"))
    assert configure_telegram() is False
    assert not C.local_config_path().exists() or "bot_token" not in C.local_config_path().read_text(encoding="utf-8")
