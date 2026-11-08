"""Datasjekker: fanger odds og resultater som ikke kan stemme, før noe trenes eller backtestes.

Ugyldige oddssett settes til tomme (NaN) i stedet for å slette kampen, slik at resultatet fortsatt brukes.
Rapporten skrives til out/data_quality.json og vises i dashboardet.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SETS = ["avg", "max", "b365", "sharp", "avgc", "sharpc"]
MARGIN = {"avg": (0.995, 1.15), "max": (0.94, 1.08), "b365": (0.99, 1.15), "sharp": (0.99, 1.10), "avgc": (0.995, 1.15), "sharpc": (0.99, 1.10)}


def clean(m: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    m = m.copy()
    rep = {"rows_in": int(len(m)), "checks": {}}
    # 1) dubletter
    key = ["league", "date", "home", "away"]
    dup = m.duplicated(key, keep="last")
    rep["checks"]["dubletter fjernet"] = int(dup.sum())
    m = m[~dup]
    # 2) umulige resultater
    if "hg" in m:
        bad = (m["hg"] > 12) | (m["ag"] > 12) | (m["hg"] < 0) | (m["ag"] < 0)
        rep["checks"]["umulige resultater fjernet"] = int(bad.sum())
        m = m[~bad]
    # 3) odds utenfor 1,01–100 og oddssett med umulig margin (1/h + 1/u + 1/b)
    for s in SETS:
        cols = [f"{s}_{k}" for k in "hda"]
        if not all(c in m for c in cols):
            continue
        o = m[cols].astype(float)
        out_range = ((o < 1.01) | (o > 100)).any(axis=1)
        over = (1 / o).sum(axis=1)
        lo, hi = MARGIN[s]
        bad = (out_range | (over < lo) | (over > hi)) & o.notna().all(axis=1)
        rep["checks"][f"{s}: oddssett satt til tomt"] = int(bad.sum())
        m.loc[bad, cols] = np.nan
        for k in ("o25", "u25"):
            c = f"{s}_{k}"
            if c in m:
                b2 = (m[c] < 1.01) | (m[c] > 30)
                m.loc[b2, c] = np.nan
    # 4) Bet365 som avviker urimelig mye fra skarp pris (typisk tastefeil i kilden) – gir falske store edges
    if all(f"b365_{k}" in m for k in "hda"):
        r = pd.concat([(m[f"b365_{k}"] / m[f"sharp_{k}"]) for k in "hda"], axis=1)
        bad = ((r > 1.35) | (r < 0.65)).any(axis=1)
        rep["checks"]["Bet365 avviker over 35 % fra skarp pris: satt til tomt"] = int(bad.sum())
        m.loc[bad, [f"b365_{k}" for k in "hda"]] = np.nan
    rep["rows_out"] = int(len(m))
    return m, rep


def write_report(rep: dict) -> None:
    (ROOT / "out").mkdir(exist_ok=True)
    rep["generated"] = pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds")
    (ROOT / "out" / "data_quality.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
