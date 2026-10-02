"""The date "today" as seen by the analysis code.

Normally the real date. The historical test (ire/backtest.py) runs the SAME analysis code as it would have
run on a past day: inside `as_of(date)` every "how old is this data" computation uses that day, so a balance
sheet that was 2 years old in 2019 is not mistaken for a recent one (or vice versa).
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

import pandas as pd

_ASOF: contextvars.ContextVar[pd.Timestamp | None] = contextvars.ContextVar("ire_asof", default=None)


def today() -> pd.Timestamp:
    d = _ASOF.get()
    return d if d is not None else pd.Timestamp.today().normalize()


@contextmanager
def as_of(date):
    token = _ASOF.set(pd.Timestamp(date).normalize())
    try:
        yield
    finally:
        _ASOF.reset(token)
