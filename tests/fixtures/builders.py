"""Builders for SYNTHETIC SEC companyfacts payloads used ONLY in unit tests.

These are not real company data: they reproduce the JSON *structure* of
https://data.sec.gov/api/xbrl/companyfacts/ so that parsing rules can be tested offline
with hand-checkable numbers.
"""
from __future__ import annotations

from collections import defaultdict


class FactsBuilder:
    def __init__(self, name="TEST CO", cik=1234567):
        self.cik = cik
        self.name = name
        self.facts = defaultdict(lambda: defaultdict(lambda: {"label": "", "description": "", "units": defaultdict(list)}))
        self._acc = 0

    def _accn(self, filed):
        self._acc += 1
        return f"0000000000-{filed[2:4]}-{self._acc:06d}"

    def add(self, concept, val, end, start=None, form="10-K", filed=None, unit="USD", tax="us-gaap", accn=None, fy=None, fp="FY"):
        filed = filed or end
        e = {"end": end, "val": val, "accn": accn or self._accn(filed), "fy": fy or int(filed[:4]), "fp": fp,
             "form": form, "filed": filed}
        if start:
            e["start"] = start
        self.facts[tax][concept]["units"][unit].append(e)
        return self

    def annual(self, concept, year_vals: dict[int, float], filed_lag_days=45, form="10-K", unit="USD", tax="us-gaap",
               instant=False, fye="12-31"):
        for y, v in year_vals.items():
            end = f"{y}-{fye}"
            start = None if instant else f"{y}-01-01" if fye == "12-31" else None
            filed = f"{y + 1}-02-15"
            self.add(concept, v, end, start=start, form=form, filed=filed, unit=unit, tax=tax)
        return self

    def build(self):
        return {
            "cik": self.cik,
            "entityName": self.name,
            "facts": {tax: {c: {"label": d["label"], "description": d["description"], "units": dict(d["units"])}
                            for c, d in concepts.items()} for tax, concepts in self.facts.items()},
        }
