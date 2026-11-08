"""Daglig: oppdater data, refit lagstyrker til i dag og skriv sannsynligheter for kommende kamper.

Output: out/predictions.json. For hvert utfall står `nt_min_odds`: laveste NT-odds som gir
spill etter gjeldende spillregel. Morgenkjøringen trenger bare å sammenligne NT-odds mot den.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import data  # noqa: E402
from base import market_features  # noqa: E402
from learn import OUT3  # noqa: E402
from models import current_elo, fit_dc, markets_from_matrix, score_matrix  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())
LABEL = {"h": "hjemmeseier", "d": "uavgjort", "a": "borteseier"}


def r4(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 4)


def main():
    if "--no-download" not in sys.argv:
        data.download()
    m = data.load_matches()
    fx = data.load_fixtures()
    today = pd.Timestamp.now(tz="Europe/Oslo").tz_localize(None).normalize()
    fx = fx[fx["date"] >= today].reset_index(drop=True)
    champ = json.loads((ROOT / "out" / "champion.json").read_text())
    with open(ROOT / "out" / "models.pkl", "rb") as fh:
        fitted = pickle.load(fh)
    if fx.empty:
        out = {"generated": pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds"), "fixtures": [],
               "note": "Ingen kommende kamper i topp 5-ligaene i fixtures.csv (f.eks. landslagspause)."}
        (ROOT / "out" / "predictions.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
        print(out["note"])
        return

    elo = current_elo(m, k=CFG["elo_k"], hfa=CFG["elo_hfa"])
    feats = []
    for lg, g in fx.groupby("league"):
        hist = m[m["league"] == lg]
        fits = {t: fit_dc(hist, today, xi=CFG["xi"], target=t, shrink=CFG["shrink"], pxg_alpha=CFG["pxg_alpha"])
                for t in ("goals", "pxg")}
        for i, r in g.iterrows():
            d = {"i": i}
            for t, p in fits.items():
                lh, la = p.rates(r["home"], r["away"])
                d.update({f"{t}_{k}": v for k, v in markets_from_matrix(score_matrix(lh, la, p.rho)).items()})
                d[f"{t}_lh"], d[f"{t}_la"] = lh, la
            d["n_home"] = fits["goals"].n_games.get(r["home"], 0)
            d["n_away"] = fits["goals"].n_games.get(r["away"], 0)
            eh, ea = elo.get((lg, r["home"]), 1450.0), elo.get((lg, r["away"]), 1450.0)
            d["elo_diff"] = (eh + CFG["elo_hfa"] - ea) / 400
            feats.append(d)
    df = fx.join(pd.DataFrame(feats).set_index("i")).join(market_features(fx))

    from learn import model_specs
    specs = model_specs()
    for name, (kind, mdl) in fitted.items():
        X = specs[name][1](df)
        ok = np.isfinite(X).all(1)
        if kind == "1x2":
            P = np.full((len(df), 3), np.nan)
            if ok.any():
                P[ok] = mdl.predict_proba(X[ok])
            for j, k in enumerate(OUT3):
                df[f"{name}_{k}"] = P[:, j]
        else:
            p = np.full(len(df), np.nan)
            if ok.any():
                p[ok] = mdl.predict_proba(X[ok])[:, 1]
            df[f"{name}_p"] = p

    rule = champ.get("bet_rule") or {}
    prob_model = champ["prob_model"]
    edge = rule.get("edge_min", CFG["edge_min"])
    rule_model = rule.get("model")

    def pcols(name):
        if name.startswith("mkt_"):
            return [f"{name}_{k}" for k in OUT3]
        if name in ("goals", "pxg"):
            return [f"{name}_p_{k}" for k in OUT3]
        return [f"{name}_{k}" for k in OUT3]

    rows = []
    for _, r in df.sort_values(["date", "time", "league"]).iterrows():
        p_model = [r.get(c) for c in pcols(prob_model)]
        p_rule = [r.get(c) for c in pcols(rule_model)] if rule_model else [np.nan] * 3
        outcomes = {}
        for k, pm, pr in zip(OUT3, p_model, p_rule):
            ok = pr is not None and np.isfinite(pr) and pr > 0
            outcomes[k] = {"label": LABEL[k], "p": r4(pm), "p_rule": r4(pr),
                           "nt_min_odds": round((1 + edge) / pr, 2) if ok else None,
                           "avg_odds": r4(r[f"avg_{k}"]), "sharp_odds": r4(r[f"sharp_{k}"])}
        rows.append({
            "league": data.LEAGUES[r["league"]], "date": r["date"].strftime("%Y-%m-%d"), "time": r.get("time"),
            "home": r["home"], "away": r["away"],
            "outcomes": outcomes,
            "over25": {"p_model": r4(r.get("ou_stack_lr_p")), "p_market": r4(r.get("mkt_sharp_o25")),
                       "avg_over": r4(r["avg_o25"]), "avg_under": r4(r["avg_u25"])},
            "btts": {"p_model": r4(r.get("btts_lr_p"))},
            "own_model": {"p_h": r4(r.get("pxg_p_h")), "p_d": r4(r.get("pxg_p_d")), "p_a": r4(r.get("pxg_p_a")),
                          "xg_home": r4(r.get("pxg_lh")), "xg_away": r4(r.get("pxg_la"))},
        })
    out = {"generated": pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds"),
           "model_version": CFG["version"], "prob_model": prob_model, "bet_rule": rule,
           "note": "p = beste kalibrerte modell. Spill et utfall bare hvis NT-odds >= nt_min_odds.",
           "fixtures": rows}
    (ROOT / "out" / "predictions.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"{len(rows)} kommende kamper skrevet. Spillregel: {rule_model} med edge >= {edge:.0%}")


if __name__ == "__main__":
    main()
