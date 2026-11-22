"""Reservekilde for kamplista: kamper morgenkjøringen fant selv (NT, FotMob, Pinnacle) når football-data.co.uk
ikke har oppdatert fixtures.csv.

Format (data/extra_fixtures.json), en liste med:
  {"league": "League One", "date": "2026-11-28", "time": "16:00", "home": "Peterborough United",
   "away": "MK Dons", "sharp": {"h": 2.1, "d": 3.4, "a": 3.5}, "avg": {"h": 2.05, "d": 3.3, "a": 3.4}}
time er norsk tid. sharp = Pinnacle (påkrevd for at sannsynlighetsmodellen skal kunne regne). avg er valgfri:
mangler den, brukes skarp pris som markedssnitt (merket i avg_from).
Lagnavn kan stå slik NT eller FotMob skriver dem: de matches mot navnene i football-data.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

NAME_TO_CODE = {"Premier League": "E0", "La Liga": "SP1", "LaLiga": "SP1", "Serie A": "I1", "Bundesliga": "D1",
                "Ligue 1": "F1", "Championship": "E1", "League One": "E2", "England League 1": "E2",
                "2. Bundesliga": "D2", "Serie B": "I2"}
# Navn der football-data forkorter så mye at fuzzy-match ikke holder
ALIAS = {"manchester united": "Man United", "manchester utd": "Man United", "manchester city": "Man City",
         "nottingham forest": "Nott'm Forest", "nottm forest": "Nott'm Forest", "sheffield wednesday": "Sheffield Weds",
         "wolverhampton": "Wolves", "wolverhampton wanderers": "Wolves", "paris saint germain": "Paris SG", "psg": "Paris SG",
         "bayern munchen": "Bayern Munich", "bayern": "Bayern Munich", "borussia monchengladbach": "M'gladbach",
         "monchengladbach": "M'gladbach", "gladbach": "M'gladbach", "borussia dortmund": "Dortmund",
         "eintracht frankfurt": "Ein Frankfurt", "atletico madrid": "Ath Madrid", "atletico": "Ath Madrid",
         "athletic bilbao": "Ath Bilbao", "athletic club": "Ath Bilbao", "real sociedad": "Sociedad",
         "rayo vallecano": "Vallecano", "espanyol": "Espanol", "deportivo la coruna": "La Coruna", "deportivo": "La Coruna",
         "racing santander": "Santander", "koln": "FC Koln", "cologne": "FC Koln", "bayer leverkusen": "Leverkusen",
         "queens park rangers": "QPR", "west bromwich albion": "West Brom", "west bromwich": "West Brom",
         "mk dons": "Milton Keynes Dons", "peterborough": "Peterboro", "peterborough united": "Peterboro",
         "hellas verona": "Verona", "internazionale": "Inter", "inter milan": "Inter", "ac milan": "Milan",
         "sudtirol": "Sudtirol", "fortuna dusseldorf": "Fortuna Dusseldorf", "arminia bielefeld": "Bielefeld",
         "eintracht braunschweig": "Braunschweig", "spvgg greuther furth": "Greuther Furth"}
DROP = {"fc", "afc", "cf", "sc", "ac", "ss", "us", "fk", "sv", "vfl", "vfb", "tsg", "rcd", "ud", "cd", "sd", "rc",
        "club", "calcio", "1", "04", "05", "96", "1899", "1846", "1907", "1913", "town", "city", "united", "athletic", "county",
        "rovers", "wanderers", "albion", "hotspur", "argyle", "de", "real", "olympique", "stade", "as", "ogc", "losc"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s.replace("'", ""))
    return re.sub(r"\s+", " ", s).strip()


def core(s: str) -> str:
    return " ".join(t for t in norm(s).split() if t not in DROP) or norm(s)


def match_team(name: str, candidates: list[str]) -> str | None:
    """Finn football-data-navnet for et lag, blant lagene i ligaen. None hvis usikkert."""
    if name in candidates:
        return name
    n = norm(name)
    if n in ALIAS and ALIAS[n] in candidates:
        return ALIAS[n]
    by_norm = {norm(c): c for c in candidates}
    if n in by_norm:
        return by_norm[n]
    by_core = {}
    for c in candidates:
        by_core.setdefault(core(c), []).append(c)
    k = core(name)
    if k in by_core and len(by_core[k]) == 1:
        return by_core[k][0]
    # ett lag der det ene navnet inneholder det andre som helt ord (Bolton Wanderers ~ Bolton)
    hits = [c for c in candidates if re.search(rf"\b{re.escape(core(c))}\b", k) or re.search(rf"\b{re.escape(k)}\b", core(c))]
    if len(hits) == 1:
        return hits[0]
    best = difflib.get_close_matches(k, list(by_core), n=2, cutoff=0.8)
    if len(best) == 1 or (len(best) == 2 and difflib.SequenceMatcher(None, k, best[0]).ratio() - difflib.SequenceMatcher(None, k, best[1]).ratio() > 0.1):
        c = by_core[best[0]]
        return c[0] if len(c) == 1 else None
    return None


def oslo_to_london(date: str, time: str | None) -> tuple[pd.Timestamp, str | None]:
    if not time:
        return pd.Timestamp(date), None
    t = pd.Timestamp(f"{date} {time}").tz_localize("Europe/Oslo").tz_convert("Europe/London")
    return t.tz_localize(None).normalize(), t.strftime("%H:%M")


def load_extra(path: Path, hist: pd.DataFrame, fx: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Gjør extra_fixtures.json om til samme radformat som data.load_fixtures(). Returnerer (rader, problemer)."""
    if not path or not Path(path).exists():
        return pd.DataFrame(), []
    items = json.loads(Path(path).read_text())
    seasons = sorted(hist["season"].astype(str).str.zfill(4).unique())[-2:]
    recent = hist[hist["season"].astype(str).str.zfill(4).isin(seasons)]
    rows, issues = [], []
    for it in items:
        code = it.get("league") if it.get("league") in NAME_TO_CODE.values() else NAME_TO_CODE.get(it.get("league"))
        if not code:
            issues.append({**{k: it.get(k) for k in ("league", "home", "away")}, "problem": "ukjent liga"})
            continue
        g = recent[recent["league"] == code]
        cand = sorted(set(g["home"]) | set(g["away"]) | (set(fx.loc[fx["league"] == code, "home"]) if len(fx) else set()))
        h, a = match_team(it["home"], cand), match_team(it["away"], cand)
        if not h or not a or h == a:
            issues.append({**{k: it.get(k) for k in ("league", "home", "away")}, "problem": "fant ikke lagnavn i football-data",
                           "matched": [h, a]})
            continue
        sharp = it.get("sharp") or {}
        if not all(isinstance(sharp.get(k), (int, float)) and sharp[k] > 1 for k in "hda"):
            issues.append({"league": it.get("league"), "home": h, "away": a, "problem": "mangler skarp pris (h, d, a)"})
            continue
        avg = it.get("avg") if all(isinstance((it.get("avg") or {}).get(k), (int, float)) for k in "hda") else None
        date, t = oslo_to_london(it["date"], it.get("time"))
        r = {"league": code, "season": None, "date": date, "time": t, "home": h, "away": a,
             "fixture_source": "morgenkjøringen", "avg_from": "avg" if avg else "sharp",
             "name_in": f"{it['home']} – {it['away']}"}
        for k in "hda":
            r[f"sharp_{k}"] = float(sharp[k])
            r[f"avg_{k}"] = float((avg or sharp)[k])
            r[f"max_{k}"] = r[f"b365_{k}"] = r[f"avgc_{k}"] = r[f"sharpc_{k}"] = np.nan
        for k in ("o25", "u25"):
            for pre in ("avg", "max", "b365", "sharp", "avgc", "sharpc"):
                r[f"{pre}_{k}"] = np.nan
        so, su = it.get("sharp_o25"), it.get("sharp_u25")
        if isinstance(so, (int, float)) and isinstance(su, (int, float)):
            r["sharp_o25"], r["sharp_u25"], r["avg_o25"], r["avg_u25"] = so, su, so, su
        rows.append(r)
    out = pd.DataFrame(rows)
    if len(out) and len(fx):
        key = ["league", "date", "home", "away"]
        have = set(map(tuple, fx[key].astype(str).to_numpy()))
        out = out[[tuple(x) not in have for x in out[key].astype(str).to_numpy()]]
    return out.reset_index(drop=True), issues
