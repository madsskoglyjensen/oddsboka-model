"""Henter og normaliserer kampdata fra football-data.co.uk.

Kilde: https://www.football-data.co.uk (gratis CSV, resultater + odds fra mange bookmakere).
Én rad per kamp, med åpnings- og sluttodds samlet i faste kolonnenavn.
"""
from __future__ import annotations

import io
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
LEAGUES = {"E0": "Premier League", "SP1": "La Liga", "I1": "Serie A", "D1": "Bundesliga", "F1": "Ligue 1"}
FIRST_SEASON = 2015  # 2015/16


def season_codes(today: pd.Timestamp | None = None) -> list[str]:
    today = today or pd.Timestamp.today()
    last = today.year if today.month >= 7 else today.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(FIRST_SEASON, last + 1)]


def _get(url: str, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "oddsboka-model/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise RuntimeError(url)


def download(refresh_current: bool = True) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    codes = season_codes()
    for s in codes:
        for lg in LEAGUES:
            f = RAW / f"{lg}_{s}.csv"
            if f.exists() and not (refresh_current and s == codes[-1]):
                continue
            f.write_bytes(_get(f"https://football-data.co.uk/mmz4281/{s}/{lg}.csv"))
    (RAW / "fixtures.csv").write_bytes(_get("https://football-data.co.uk/fixtures.csv"))


def _first(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Første kolonne som finnes og har verdi, rad for rad."""
    out = pd.Series(np.nan, index=df.index)
    for c in cols:
        if c in df.columns:
            out = out.fillna(pd.to_numeric(df[c], errors="coerce"))
    return out


def _read(f: Path) -> pd.DataFrame:
    d = pd.read_csv(f, encoding="latin1", on_bad_lines="skip")
    d.columns = [c.replace("\u00ef\u00bb\u00bf", "").replace("\ufeff", "").strip() for c in d.columns]
    return d


def _normalise(d: pd.DataFrame, league: str, season: str | None) -> pd.DataFrame:
    d = d.dropna(subset=["HomeTeam", "AwayTeam"]).copy()
    o = pd.DataFrame(index=d.index)
    o["league"] = league
    o["season"] = season
    o["date"] = pd.to_datetime(d["Date"], dayfirst=True, errors="coerce", format="mixed")
    if "Time" in d.columns:
        o["time"] = d["Time"].astype(str)
    o["home"] = d["HomeTeam"].str.strip()
    o["away"] = d["AwayTeam"].str.strip()
    for a, b in [("hg", "FTHG"), ("ag", "FTAG"), ("hs", "HS"), ("as_", "AS"), ("hst", "HST"), ("ast", "AST"), ("hc", "HC"), ("ac", "AC")]:
        o[a] = pd.to_numeric(d[b], errors="coerce") if b in d.columns else np.nan
    for k, s in zip("hda", "HDA"):
        # Snitt av markedet før kamp (proxy for en vanlig bookmaker som NT)
        o[f"avg_{k}"] = _first(d, [f"Avg{s}", f"BbAv{s}"])
        o[f"max_{k}"] = _first(d, [f"Max{s}", f"BbMx{s}"])
        o[f"b365_{k}"] = _first(d, [f"B365{s}"])
        # Skarp pris før kamp: Pinnacle, ellers Betfair Exchange
        o[f"sharp_{k}"] = _first(d, [f"PS{s}", f"BFE{s}"])
        # Sluttodds
        o[f"avgc_{k}"] = _first(d, [f"AvgC{s}"])
        o[f"sharpc_{k}"] = _first(d, [f"PSC{s}", f"BFEC{s}"])
    for k, s in [("o", ">2.5"), ("u", "<2.5")]:
        o[f"avg_{k}25"] = _first(d, [f"Avg{s}", f"BbAv{s}"])
        o[f"max_{k}25"] = _first(d, [f"Max{s}", f"BbMx{s}"])
        o[f"b365_{k}25"] = _first(d, [f"B365{s}"])
        o[f"sharp_{k}25"] = _first(d, [f"P{s}", f"BFE{s}"])
        o[f"avgc_{k}25"] = _first(d, [f"AvgC{s}"])
        o[f"sharpc_{k}25"] = _first(d, [f"PC{s}", f"BFEC{s}"])
    return o


def load_matches() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW.glob("*_*.csv")):
        lg, s = f.stem.split("_")
        if lg not in LEAGUES:
            continue
        d = _read(f)
        if d.empty:
            continue
        frames.append(_normalise(d, lg, s))
    m = pd.concat(frames, ignore_index=True)
    m = m.dropna(subset=["date", "hg", "ag"]).sort_values(["date", "league", "home"]).reset_index(drop=True)
    m["hg"] = m["hg"].astype(int)
    m["ag"] = m["ag"].astype(int)
    return m


def load_fixtures() -> pd.DataFrame:
    f = RAW / "fixtures.csv"
    if not f.exists():
        return pd.DataFrame()
    d = _read(f)
    d = d[d["Div"].isin(LEAGUES)]
    out = pd.concat([_normalise(g, lg, None) for lg, g in d.groupby("Div")], ignore_index=True) if len(d) else pd.DataFrame()
    return out


if __name__ == "__main__":
    if "--no-download" not in sys.argv:
        download()
    m = load_matches()
    m.to_csv(ROOT / "data" / "matches.csv", index=False)
    print(f"{len(m)} kamper, {m['date'].min().date()} til {m['date'].max().date()}")
    print(m.groupby("season").size().to_string())
