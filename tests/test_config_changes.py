"""Configuration validation, `configure`, and the run-to-run change monitor."""
import json
from pathlib import Path

import pytest

import ire.config as C
from ire.changes import compute_changes
from ire.db import init_db
from ire.scoring import C_AVG, C_QFAIR

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------------------------- config
def _cfg(tmp_path, replace=None, local=None):
    text = (ROOT / "config.toml").read_text(encoding="utf-8")
    for a, b in (replace or {}).items():
        assert a in text
        text = text.replace(a, b)
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    if local is not None:
        (tmp_path / "config.local.toml").write_text(local, encoding="utf-8")
    return p


def test_repo_config_is_valid_and_has_no_contact():
    cfg = C.load_config(ROOT / "config.toml")
    assert cfg.get("sec.user_agent") == ""            # the SEC contact never goes on GitHub
    assert "config.local.toml" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_invalid_mode_is_rejected(tmp_path):
    p = _cfg(tmp_path, {'mode = "standard"': 'mode = "veloce"'})
    with pytest.raises(C.ConfigError, match="universe.mode"):
        C.load_config(p)


def test_inconsistent_portfolio_limits_rejected(tmp_path):
    p = _cfg(tmp_path, {"min_weight = 0.025": "min_weight = 0.2"})
    with pytest.raises(C.ConfigError, match="min_weight"):
        C.load_config(p)


def test_windows_path_gives_italian_hint(tmp_path):
    p = _cfg(tmp_path, {'data_dir = "data"': 'data_dir = "C:\\Users\\aless\\dati"'})
    with pytest.raises(C.ConfigError) as e:
        C.load_config(p)
    assert "barre normali" in str(e.value)


def test_local_file_overrides_contact(tmp_path):
    p = _cfg(tmp_path, local='[sec]\nuser_agent = "Nome Cognome nome@example.com"\n')
    cfg = C.load_config(p)
    assert cfg.sec_user_agent == "Nome Cognome nome@example.com"
    assert cfg.get("sec.max_requests_per_second") == 8     # other keys of the section are kept


def test_configure_writes_local_file_only(tmp_path, monkeypatch):
    """Bug §7.8: with a non-empty but invalid user_agent, `configure` printed "Salvato" without writing."""
    import ire.__main__ as M

    p = _cfg(tmp_path, {'user_agent = ""': 'user_agent = "senza-email"'})
    before = p.read_text(encoding="utf-8")
    monkeypatch.setattr(C, "CONFIG_PATH", p)
    monkeypatch.setattr(C, "_CONFIG", None)
    monkeypatch.setattr(M.sys.stdin, "isatty", lambda: True, raising=False)
    answers = iter(["Nome Cognome", "nome@example.com"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert M.configure() is True
    assert p.read_text(encoding="utf-8") == before                      # config.toml untouched
    assert "nome@example.com" in (tmp_path / "config.local.toml").read_text(encoding="utf-8")
    assert C.load_config(reload=True).sec_user_agent == "Nome Cognome nome@example.com"
    monkeypatch.setattr(C, "_CONFIG", None)


def test_configure_check_only(tmp_path, monkeypatch):
    import ire.__main__ as M

    p = _cfg(tmp_path)
    monkeypatch.setattr(C, "CONFIG_PATH", p)
    monkeypatch.setattr(C, "_CONFIG", None)
    assert M.configure(check_only=True) is False
    monkeypatch.setattr(C, "_CONFIG", None)


# ---------------------------------------------------------------------------------------------- changes
@pytest.fixture()
def con(tmp_path):
    c = init_db(tmp_path / "c.sqlite")
    rows = [("A", "AAA", 1, None), ("B", "BBB", 0, "liquidità insufficiente (mediana 1 M$/giorno)"),
            ("T", "TTT", 0, "dati SEC non scaricabili: timeout"), ("N", "NNN", 1, None)]
    c.executemany("INSERT INTO companies (company_id, ticker, name, in_universe, exclusion_reason) VALUES (?,?,?,?,?)",
                  [(a, b, b, d, e) for a, b, d, e in rows])
    return c


def _run(con, rid, mode, scores, flags=()):
    con.execute("INSERT INTO runs (run_id, mode, status) VALUES (?,?, 'completed')", (rid, mode))
    for cid, cls, deep in scores:
        con.execute("INSERT INTO scores (run_id, company_id, robust_score, classification, valuation_verdict, detail) "
                    "VALUES (?,?,?,?,?,?)", (rid, cid, 60.0, cls, "costosa", json.dumps({"deep_analysis": deep})))
    for cid, code, sev in flags:
        con.execute("INSERT INTO flags (run_id, company_id, code, severity, message) VALUES (?,?,?,?,?)",
                    (rid, cid, code, sev, f"{code} msg"))


def test_text_flag_not_new_when_company_just_entered_deep_analysis(con):
    _run(con, 1, "standard", [("A", C_AVG, False), ("N", C_AVG, True)])
    _run(con, 2, "standard", [("A", C_AVG, True), ("N", C_AVG, True)],
         flags=[("A", "CUSTOMER_CONCENTRATION", "medium"), ("N", "IMPAIRMENT_TEXT", "medium"),
                ("A", "LATE_FILING", "medium")])
    ch = compute_changes(con, 2, 1)
    new = ch[ch.tipo == "Nuova segnalazione"]
    assert set(new.cambiamento) == {"IMPAIRMENT_TEXT msg", "LATE_FILING msg"}


def test_temporary_exclusion_vs_exit(con):
    _run(con, 1, "standard", [("A", C_AVG, True), ("B", C_AVG, True), ("T", C_AVG, True)])
    _run(con, 2, "standard", [("A", C_QFAIR, True)])
    ch = compute_changes(con, 2, 1).set_index("ticker")
    assert ch.loc["BBB", "cambiamento"].startswith("Uscita dall'universo") and ch.loc["BBB", "gravità"] == "media"
    assert ch.loc["TTT", "cambiamento"].startswith("Non analizzata") and ch.loc["TTT", "gravità"] == "info"


def test_different_modes_do_not_report_universe_moves(con):
    _run(con, 1, "quick", [("A", C_AVG, True), ("B", C_AVG, True)])
    _run(con, 2, "standard", [("A", C_AVG, True), ("N", C_AVG, True)])
    ch = compute_changes(con, 2, 1)
    assert ch.empty or "Universo" not in set(ch.tipo)
