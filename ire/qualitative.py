"""Qualitative analysis of SEC filings (free): event filings (8-K items), late filings,
and text analysis of the annual report (10-K / 20-F):
  * red-flag phrases (going concern, material weakness, restatement) with negation checks;
  * customer / supplier concentration and geopolitical exposure mentions;
  * risk-factor section diff vs. the previous annual report (what is NEW this year).
Every finding keeps a short evidence snippet and the URL of the filing so the user can verify.
Text heuristics can misfire: findings are labelled "da verificare" where appropriate.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

from .sources import sec

ITEM_FLAGS = {
    "1.03": ("BANKRUPTCY_8K", "severe", "8-K item 1.03: procedura di bancarotta o amministrazione controllata"),
    "4.02": ("NON_RELIANCE_8K", "severe",
             "8-K item 4.02: i bilanci precedentemente pubblicati non sono più affidabili (restatement)"),
    "3.01": ("DELISTING_NOTICE_8K", "high", "8-K item 3.01: notifica di delisting o mancato rispetto requisiti di quotazione"),
    "4.01": ("AUDITOR_CHANGE_8K", "medium", "8-K item 4.01: cambio del revisore contabile"),
    "2.06": ("IMPAIRMENT_8K", "medium", "8-K item 2.06: svalutazioni rilevanti di attività"),
    "1.05": ("CYBER_INCIDENT_8K", "medium", "8-K item 1.05: incidente di cybersecurity rilevante"),
    "2.04": ("DEBT_ACCELERATION_8K", "high", "8-K item 2.04: eventi che accelerano obbligazioni finanziarie"),
}


def filings_flags(company_id: str, cik: str, sub: dict[str, Any], months: int = 24) -> tuple[list[dict], list[dict]]:
    """Returns (filings rows for DB, flags)."""
    rows, flags = [], []
    cutoff = (date.today() - timedelta(days=int(months * 30.4))).isoformat()
    exec_changes = 0
    for f in sec.recent_filings(sub):
        acc, form, filed = f.get("accessionNumber"), f.get("form"), f.get("filingDate") or ""
        if not acc:
            continue
        doc = f.get("primaryDocument") or ""
        url = sec.filing_url(cik, acc, doc) if doc else sec.filing_index_url(cik, acc)
        if filed >= (date.today() - timedelta(days=5 * 366)).isoformat():
            rows.append({"company_id": company_id, "accession": acc, "form": form, "filed": filed,
                         "report_date": f.get("reportDate"), "items": f.get("items"), "primary_doc": doc, "url": url})
        if filed < cutoff:
            continue
        if form in ("8-K", "8-K/A", "6-K") and f.get("items"):
            for it in str(f["items"]).split(","):
                it = it.strip()
                if it in ITEM_FLAGS:
                    code, sev, msg = ITEM_FLAGS[it]
                    flags.append({"code": code, "severity": sev, "message": f"{msg} (depositato il {filed})",
                                  "evidence": {"url": url, "filed": filed, "form": form}, "source": "SEC EDGAR submissions"})
                if it == "5.02":
                    exec_changes += 1
        if form in ("NT 10-K", "NT 10-Q", "NT 20-F"):
            flags.append({"code": "LATE_FILING", "severity": "medium",
                          "message": f"Deposito tardivo ({form}) il {filed}: la società non ha rispettato la scadenza",
                          "evidence": {"url": url}, "source": "SEC EDGAR submissions"})
    if exec_changes >= 3:
        flags.append({"code": "EXEC_TURNOVER", "severity": "info",
                      "message": f"{exec_changes} comunicazioni 8-K item 5.02 (cambi di amministratori/dirigenti) in {months} mesi",
                      "source": "SEC EDGAR submissions"})
    return rows, flags


# ---------------------------------------------------------------- text analysis
def html_to_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        # hidden iXBRL header
        for tag in soup.find_all(["ix:header"]):
            tag.decompose()
        text = soup.get_text("\n")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = _clean(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


RISK_START_10K = re.compile(r"item\s*1a\s*[\.\:\-–—]?\s*risk\s+factors", re.I)
RISK_END_10K = re.compile(r"item\s*1b\s*[\.\:\-–—]?\s*unresolved|item\s*1c\s*[\.\:\-–—]?\s*cybersecurity|item\s*2\s*[\.\:\-–—]?\s*properties", re.I)
RISK_START_20F = re.compile(r"\bD\.\s*risk\s+factors|item\s*3\.?\s*d\.?\s*risk\s+factors|\brisk\s+factors\b", re.I)
RISK_END_20F = re.compile(r"item\s*4\s*[\.\:\-–—]?\s*information\s+on\s+the\s+company", re.I)


def extract_risk_section(text: str, form: str) -> str:
    if form.startswith("20-F"):
        start_re, end_re = RISK_START_20F, RISK_END_20F
    else:
        start_re, end_re = RISK_START_10K, RISK_END_10K
    best = ""
    for m in start_re.finditer(text):
        e = end_re.search(text, m.end())
        if not e:
            continue
        seg = text[m.end(): e.start()]
        if len(seg) > len(best):
            best = seg
    return best if len(best) > 2000 else ""


SENT_SPLIT = re.compile(r"(?<=[\.\!\?])\s+(?=[A-Z])")


def _norm_sentence(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\d+", "#", s)
    s = re.sub(r"[^a-z# ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def sentence_set(section: str) -> dict[str, str]:
    out = {}
    for sent in SENT_SPLIT.split(section.replace("\n", " ")):
        sent = sent.strip()
        if len(sent) < 60:
            continue
        key = hashlib.sha1(_norm_sentence(sent).encode()).hexdigest()
        out[key] = sent
    return out


# ---------------------------------------------------------------- red-flag sentences
# Each red flag is searched sentence by sentence. Risk-factor boilerplate is hypothetical
# ("we MAY identify material weaknesses in the future", "IF we cannot refinance ... COULD raise
# substantial doubt"): such sentences are discarded, while factual ones ("these conditions raise
# substantial doubt", "ICFR was NOT EFFECTIVE because of a material weakness") are kept.
HYPOTHETICAL = re.compile(
    r"\b(may|might|could|would|if|unless|should|in the event|in the future|there can be no assurance|no assurance|"
    r"cannot assure|can not assure|potential(ly)?|possible|possibility|risk that|failure to|fail to)\b", re.I)
SENT_ANY = re.compile(r"(?<=[\.\!\?;])\s+")


def _clean(text: str) -> str:
    return (text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u00a0", " "))


def _sentences(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", x).strip() for x in SENT_ANY.split(_clean(text).replace("\n", " ")) if len(x) > 20]


GC_ANY = re.compile(r"substantial doubt.{0,250}?going concern|going concern.{0,250}?substantial doubt", re.I)
GC_NEG = re.compile(r"(\bno\b|\bnever\b|\bnot raise|alleviat\w*|mitigat\w*).{0,40}substantial doubt|substantial doubt.{0,120}"
                    r"(alleviated|has been alleviated|no longer|did not exist|does not exist)", re.I)
GC_FACT = re.compile(r"\b(raises?|raised|exists?|existed|there is|conclu\w+|determined|express\w*)\b.{0,80}substantial doubt|"
                     r"substantial doubt (exists|existed|about)", re.I)

MW_ANY = re.compile(r"material weakness(es)?", re.I)
MW_FACT = re.compile(r"not effective|ineffective|(identified|existed|exists|constitutes?|resulted in|due to|because of).{0,80}"
                     r"material weakness|material weakness(es)?.{0,120}(identified|existed|exists|has not been remediated|"
                     r"have not been remediated|not yet (been )?remediated|remains?|was|were)|remediat\w+ (of )?(the|this|these|our) "
                     r"material weakness", re.I)
MW_NEG = re.compile(r"(\bno\b|did not identify|not identified|were not any|was not any|there were no).{0,60}material weakness", re.I)

RESTATE = re.compile(r"\b(restated|restatement of|restate)\b.{0,160}financial statements|non-reliance", re.I)
RESTATE_NEG = re.compile(r"(\bno\b|\bnot\b|\bnever\b).{0,30}(restate|restatement)", re.I)

AUDITOR = re.compile(r"(dismiss\w*|resign\w*|declin\w* to stand).{0,200}(independent registered public accounting firm|"
                     r"independent auditor|statutory auditor)|engaged.{0,120}as (our|its|the company's) new independent "
                     r"(registered public accounting firm|auditor)", re.I)
IMPAIR = re.compile(r"(goodwill|intangible assets?).{0,120}impairment (charge|loss)(es)?.{0,80}\$\s?[\d.,]+\s?"
                    r"(million|billion)|impairment (charge|loss)(es)?.{0,60}\$\s?[\d.,]+\s?(million|billion).{0,80}"
                    r"(goodwill|intangible)", re.I)


def find_facts(text: str, any_re: re.Pattern, fact_re: re.Pattern | None, neg_re: re.Pattern | None,
               allow_hypothetical_if_fact: bool = False) -> list[str]:
    """Sentences matching any_re that are factual (fact_re), not negated, not hypothetical."""
    hits = []
    for sent in _sentences(text):
        if not any_re.search(sent):
            continue
        if neg_re is not None and neg_re.search(sent):
            continue
        is_fact = fact_re.search(sent) if fact_re is not None else True
        if not is_fact:
            continue
        if HYPOTHETICAL.search(sent) and not allow_hypothetical_if_fact:
            continue
        hits.append(sent[:500])
    return hits


def _affirmative(text: str, pattern: re.Pattern, lookback: int = 120) -> list[str]:
    """Backward-compatible helper: factual, non-negated, non-hypothetical sentences matching pattern."""
    return find_facts(text, pattern, None, re.compile(r"(\bno\b|\bnot\b|without|absence of)[^.]{0,40}" + pattern.pattern, re.I))


def text_red_flags(t0: str, source: str, url: str) -> list[dict[str, Any]]:
    ev = lambda snip: {"url": url, "snippet": snip[:400]}  # noqa: E731
    out = []
    gc = find_facts(t0, GC_ANY, GC_FACT, GC_NEG)
    if gc:
        out.append({"code": "GOING_CONCERN_TEXT", "severity": "high",
                    "message": "Il report annuale dichiara un 'dubbio sostanziale sulla continuità aziendale' (da verificare)",
                    "evidence": ev(gc[0]), "source": source})
    # an existing weakness is often described with "if we fail to remediate the material weakness identified…"
    mw = find_facts(t0, MW_ANY, MW_FACT, MW_NEG, allow_hypothetical_if_fact=False)
    mw += [x for x in find_facts(t0, MW_ANY, re.compile(r"(the|this|these) material weakness(es)?[^.]{0,60}(identified|described)|"
                                                          r"not effective", re.I), MW_NEG, allow_hypothetical_if_fact=True)
           if x not in mw]
    if mw:
        out.append({"code": "MATERIAL_WEAKNESS", "severity": "high",
                    "message": "Debolezza materiale nei controlli interni sul bilancio dichiarata nel report (da verificare)",
                    "evidence": ev(mw[0]), "source": source})
    rs = find_facts(t0, RESTATE, None, RESTATE_NEG)
    if rs:
        out.append({"code": "RESTATEMENT_TEXT", "severity": "medium",
                    "message": "Riferimento a bilanci precedenti riesposti (restatement) (da verificare)",
                    "evidence": ev(rs[0]), "source": source})
    au = find_facts(t0, AUDITOR, None, None)
    if au:
        out.append({"code": "AUDITOR_CHANGE_TEXT", "severity": "medium",
                    "message": "Cambio o dimissioni del revisore contabile menzionati nel report (da verificare)",
                    "evidence": ev(au[0]), "source": source})
    im = find_facts(t0, IMPAIR, None, None)
    if im:
        out.append({"code": "IMPAIRMENT_TEXT", "severity": "medium",
                    "message": "Svalutazione di avviamento/intangibili registrata (importo nel testo; da verificare)",
                    "evidence": ev(im[0]), "source": source})
    return out


CUSTOMER_CONC = re.compile(
    r"(?:one|two|three|a single|our largest|largest|single|each of (?:two|three)) (?:customer|client|distributor)s?[^.]{0,160}?"
    r"(?:accounted for|represented|comprised|made up)[^.]{0,60}?(\d{1,2}(?:\.\d+)?)\s?%", re.I)
SOLE_SOURCE = re.compile(r"\b(sole|single)[- ]source(d)? (supplier|vendor|manufacturer)s?", re.I)
GEO = {"Cina": re.compile(r"\bChina\b|\bChinese\b|\bPRC\b"), "Taiwan": re.compile(r"\bTaiwan\b"),
       "Russia": re.compile(r"\bRussia\b|\bRussian\b"), "Israele": re.compile(r"\bIsrael\b")}
LITIGATION = re.compile(r"class action|antitrust|securities litigation|investigation by the (SEC|DOJ|Department of Justice|FTC)", re.I)


def analyze_annual_reports(cik: str, sub: dict[str, Any]) -> dict[str, Any]:
    """Downloads the two latest annual reports (10-K or 20-F) and analyzes their text."""
    annual = [f for f in sec.recent_filings(sub) if f.get("form") in ("10-K", "20-F") and f.get("primaryDocument")]
    annual.sort(key=lambda f: f.get("filingDate") or "", reverse=True)
    result: dict[str, Any] = {"as_of": date.today().isoformat(), "filings": [], "flags": [], "notes": [], "performed": True}
    if not annual:
        result["notes"].append("Nessun 10-K/20-F con documento principale trovato")
        return result
    texts = []
    for f in annual[:2]:
        url = sec.filing_url(cik, f["accessionNumber"], f["primaryDocument"])
        try:
            html = sec.fetch_document(url)
        except Exception as e:  # noqa: BLE001
            result["notes"].append(f"Impossibile scaricare {url}: {e}")
            continue
        text = html_to_text(html)
        texts.append((f, url, text))
        result["filings"].append({"form": f["form"], "filed": f["filingDate"], "url": url, "chars": len(text)})
    if not texts:
        return result
    f0, url0, t0 = texts[0]
    ev = lambda snip: {"url": url0, "snippet": snip[:400]}  # noqa: E731

    result["flags"].extend(text_red_flags(t0, f"SEC {f0['form']} {f0['filingDate']}", url0))
    conc = [(float(m.group(1)), m.group(0)) for m in CUSTOMER_CONC.finditer(t0)]
    conc = [c for c in conc if 5 <= c[0] <= 100]
    if conc:
        mx = max(conc, key=lambda c: c[0])
        result["customer_concentration_max_pct"] = mx[0]
        sev = "medium" if mx[0] >= 20 else "info"
        result["flags"].append({"code": "CUSTOMER_CONCENTRATION", "severity": sev,
                                "message": f"Concentrazione clienti: fino al {mx[0]:.0f}% dei ricavi da uno o pochi clienti",
                                "evidence": ev(re.sub(r'\s+', ' ', mx[1])), "source": f"SEC {f0['form']} {f0['filingDate']}"})
    ss = SOLE_SOURCE.findall(t0)
    if len(ss) >= 2:
        result["flags"].append({"code": "SOLE_SOURCE_SUPPLIERS", "severity": "info",
                                "message": f"Dipendenza da fornitori unici menzionata {len(ss)} volte",
                                "source": f"SEC {f0['form']} {f0['filingDate']}"})
    risk0 = extract_risk_section(t0, f0["form"])
    result["risk_section_chars"] = len(risk0)
    if risk0:
        geo = {k: len(p.findall(risk0)) for k, p in GEO.items()}
        result["geo_mentions"] = geo
        for k, n in geo.items():
            if n >= 15:
                result["flags"].append({"code": f"GEO_EXPOSURE_{k.upper()}", "severity": "info",
                                        "message": f"{k} citata {n} volte nei fattori di rischio: esposizione geopolitica rilevante",
                                        "source": f"SEC {f0['form']} {f0['filingDate']}"})
        lit = LITIGATION.findall(risk0)
        result["litigation_mentions"] = len(lit)
    else:
        result["notes"].append("Sezione 'Risk Factors' non individuata automaticamente")
    if len(texts) >= 2 and risk0:
        f1, url1, t1 = texts[1]
        risk1 = extract_risk_section(t1, f1["form"])
        if risk1:
            s0, s1 = sentence_set(risk0), sentence_set(risk1)
            new_keys = [k for k in s0 if k not in s1]
            removed = [k for k in s1 if k not in s0]
            new_sents = sorted((s0[k] for k in new_keys), key=len, reverse=True)
            result["risk_diff"] = {
                "current": {"form": f0["form"], "filed": f0["filingDate"], "url": url0, "sentences": len(s0)},
                "previous": {"form": f1["form"], "filed": f1["filingDate"], "url": url1, "sentences": len(s1)},
                "new_count": len(new_keys), "removed_count": len(removed),
                "new_share": len(new_keys) / max(len(s0), 1),
                "new_examples": [s[:320] for s in new_sents[:8]],
            }
            if len(new_keys) / max(len(s0), 1) > 0.25:
                result["flags"].append({"code": "RISK_FACTORS_CHANGED", "severity": "info",
                                        "message": f"{len(new_keys)} frasi nuove nei fattori di rischio rispetto all'anno precedente "
                                                   f"({len(new_keys) / max(len(s0), 1):.0%}): leggere le novità",
                                        "source": f"SEC {f0['form']} vs {f1['form']}"})
    return result
