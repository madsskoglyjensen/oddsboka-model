"""Walk-forward: grunnmodellenes sannsynligheter for hver kamp, trent bare på data før kampuka.

Kjør:  python src/base.py            (alle sesonger)
       python src/base.py --from 2627 (bare fra en sesong, brukes daglig)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from models import devig, elo_ratings, fit_dc, markets_from_matrix, score_matrix  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())


def base_features(matches: pd.DataFrame, targets: pd.DataFrame, cfg: dict = CFG) -> pd.DataFrame:
    """Beregn grunnmodell-features for `targets` (kamper), med historikk fra `matches`.

    Refit én gang per liga per uke (mandag som referanse), så ingen kamp ser sin egen uke.
    """
    rows = []
    for lg, tg in targets.groupby("league"):
        hist = matches[matches["league"] == lg]
        weeks = tg["date"].dt.to_period("W-SUN").dt.start_time
        for wk, g in tg.groupby(weeks):
            ref = pd.Timestamp(wk)
            fits = {t: fit_dc(hist, ref, xi=cfg["xi"], target=t, shrink=cfg["shrink"], pxg_alpha=cfg["pxg_alpha"])
                    for t in ("goals", "pxg")}
            for idx, r in g.iterrows():
                d = {"idx": idx}
                for t, p in fits.items():
                    if p is None:
                        continue
                    lh, la = p.rates(r["home"], r["away"])
                    mk = markets_from_matrix(score_matrix(lh, la, p.rho))
                    d.update({f"{t}_{k}": v for k, v in mk.items()})
                    d[f"{t}_lh"], d[f"{t}_la"] = lh, la
                    if t == "goals":
                        d["n_home"] = p.n_games.get(r["home"], 0)
                        d["n_away"] = p.n_games.get(r["away"], 0)
                rows.append(d)
    f = pd.DataFrame(rows).set_index("idx")
    return f


def market_features(df: pd.DataFrame) -> pd.DataFrame:
    o = pd.DataFrame(index=df.index)
    for src in ("avg", "sharp", "sharpc", "avgc"):
        ph, pd_, pa = devig(df[f"{src}_h"], df[f"{src}_d"], df[f"{src}_a"])
        o[f"mkt_{src}_h"], o[f"mkt_{src}_d"], o[f"mkt_{src}_a"] = ph, pd_, pa
        if f"{src}_o25" in df:
            po, pu = devig(df[f"{src}_o25"], df[f"{src}_u25"])
            o[f"mkt_{src}_o25"] = po
    return o


def main():
    m = pd.read_csv(ROOT / "data" / "matches.csv", parse_dates=["date"])
    start = sys.argv[sys.argv.index("--from") + 1] if "--from" in sys.argv else "1617"
    targets = m[m["season"].astype(str).str.zfill(4) >= start]
    t0 = time.time()
    e = elo_ratings(m, k=CFG["elo_k"], hfa=CFG["elo_hfa"])
    b = base_features(m, targets)
    out = m.loc[targets.index].join(e).join(b).join(market_features(m.loc[targets.index]))
    out["elo_diff"] = (out["elo_home"] + CFG["elo_hfa"] - out["elo_away"]) / 400
    out.to_csv(ROOT / "out" / "base_preds.csv", index=False)
    print(f"{len(out)} kamper på {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
