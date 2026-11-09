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
    from validate import clean
    m, _ = clean(data.load_matches())
    fx, _ = clean(data.load_fixtures())
    today = pd.Timestamp.now(tz="Europe/Oslo").tz_localize(None).normalize()
    fx = fx[fx["date"] >= today].reset_index(drop=True)
    champ = json.loads((ROOT / "out" / "champion.json").read_text())
    with open(ROOT / "out" / "models.pkl", "rb") as fh:
        fitted = pickle.load(fh)
    head = header(champ)
    if fx.empty:
        out = {**head, "fixtures": [],
               "note": "Ingen kommende kamper i ligaene i fixtures.csv (f.eks. landslagspause). Spillregel og innsats gjelder som vanlig."}
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
                d[f"{t}_lh"], d[f"{t}_la"], d[f"{t}_rho"] = lh, la, p.rho
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
        allowed = champ.get("leagues") is None or r["league"] in champ["leagues"]
        for k, pm, pr in zip(OUT3, p_model, p_rule):
            ok = allowed and pr is not None and np.isfinite(pr) and pr > 0
            outcomes[k] = {"label": LABEL[k], "p": r4(pm), "p_rule": r4(pr),
                           "nt_min_odds": round((1 + edge) / pr, 2) if ok else None,
                           "avg_odds": r4(r[f"avg_{k}"]), "sharp_odds": r4(r[f"sharp_{k}"])}
        rows.append({
            "league": data.LEAGUES[r["league"]], "league_in_rule": bool(allowed), **oslo_time(r),
            "home": r["home"], "away": r["away"],
            "outcomes": outcomes,
            "over25": {"p_model": r4(r.get("ou_stack_lr_p")), "p_market": r4(r.get("mkt_sharp_o25")),
                       "avg_over": r4(r["avg_o25"]), "avg_under": r4(r["avg_u25"])},
            "btts": {"p_model": r4(r.get("btts_lr_p"))},
            "own_model": {"p_h": r4(r.get("pxg_p_h")), "p_d": r4(r.get("pxg_p_d")), "p_a": r4(r.get("pxg_p_a")),
                          "xg_home": r4(r.get("pxg_lh")), "xg_away": r4(r.get("pxg_la"))},
        })
    out = {**head, "note": "p = beste kalibrerte modell. Spill et utfall bare hvis NT-odds >= nt_min_odds. time og kickoff er norsk tid.",
           "fixtures": rows}
    (ROOT / "out" / "predictions.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    write_viz_fixtures(df, specs, fitted)
    print(f"{len(rows)} kommende kamper skrevet. Spillregel: {rule_model} med edge >= {edge:.0%}")


def header(champ: dict) -> dict:
    """Felter som alltid skal med, også når det ikke er kamper (kjøringene leser regel og innsats herfra)."""
    rule = champ.get("bet_rule") or {}
    return {"generated": pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds"), "time_zone": "Europe/Oslo",
            "model_version": CFG["version"], "prob_model": champ.get("prob_model"), "bet_rule": rule,
            "staking": champ.get("staking", "kelly_25"), "staking_label": champ.get("staking_label", "¼ Kelly"),
            "staking_spec": champ.get("staking_spec", {"kelly_mult": [[0.0, 0.25]]}),
            "leagues_in_rule": [data.LEAGUES[lg] for lg in (champ.get("leagues") or [])]}


def oslo_time(r) -> dict:
    """football-data.co.uk oppgir avspark i britisk tid. Gjør om til norsk tid og ISO med tidssone."""
    d, t = r["date"].strftime("%Y-%m-%d"), str(r.get("time") or "").strip()
    if not t or t.lower() == "nan":
        return {"date": d, "time": None, "kickoff": None}
    try:
        ko = pd.Timestamp(f"{d} {t}").tz_localize("Europe/London").tz_convert("Europe/Oslo")
        return {"date": ko.strftime("%Y-%m-%d"), "time": ko.strftime("%H:%M"), "kickoff": ko.isoformat()}
    except Exception:  # noqa: BLE001
        return {"date": d, "time": t or None, "kickoff": None}


def write_viz_fixtures(df: pd.DataFrame, specs: dict, fitted: dict) -> None:
    """Rådata for ML-laben i dashboardet: feature-vektorer og modellsannsynligheter per kommende kamp."""
    fx = []
    df = df.sort_values(["date", "time", "league"]).reset_index(drop=True)
    XS = {name: specs[name][1](df).astype(float) for name in ("stack_lr", "stack_sharp_lr")}
    for i, r in df.iterrows():
        row = {"league": data.LEAGUES[r["league"]], **oslo_time(r),
               "home": r["home"], "away": r["away"], "x": {}, "p": {}}
        for t in ("goals", "pxg"):
            row[t] = {"lh": r4(r[f"{t}_lh"]), "la": r4(r[f"{t}_la"]), "rho": r4(r[f"{t}_rho"])}
        for name in ("stack_lr", "stack_sharp_lr"):
            X = XS[name][i]
            row["x"][name] = [r4(v) for v in X] if np.isfinite(X).all() else None
        for name in ("stack_lr", "stack_sharp_lr", "stack_lgbm", "stack_rf", "stack_mlp", "elo_lr", "own_lr"):
            v = [r.get(f"{name}_{k}") for k in OUT3]
            row["p"][name] = [r4(x) for x in v] if all(x is not None and np.isfinite(x) for x in v) else None
        for name in ("goals", "pxg"):
            row["p"][name] = [r4(r[f"{name}_p_{k}"]) for k in OUT3]
        for name in ("mkt_sharp", "mkt_avg"):
            v = [r.get(f"{name}_{k}") for k in OUT3]
            row["p"][name] = [r4(x) for x in v] if all(x is not None and np.isfinite(x) for x in v) else None
        row["odds"] = {"avg": [r4(r[f"avg_{k}"]) for k in OUT3], "sharp": [r4(r[f"sharp_{k}"]) for k in OUT3]}
        row["elo_diff"] = r4(r["elo_diff"])
        fx.append(row)
    (ROOT / "out" / "viz_fixtures.json").write_text(json.dumps(fx, ensure_ascii=False))


if __name__ == "__main__":
    main()
