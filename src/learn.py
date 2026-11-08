"""Ukentlig læring: stacking-modeller, walk-forward-evaluering, betting-simulering og valg av mester.

Alt evalueres sesong for sesong: modellen for sesong S trenes bare på sesonger før S.
Skriver out/metrics.json (til dashboardet) og out/oos_preds.csv.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())
EPS = 1e-6
OUT3 = ["h", "d", "a"]

try:
    import lightgbm as lgb
except Exception:  # noqa: BLE001
    lgb = None


def lg(p):
    return np.log(np.clip(p, EPS, 1 - EPS))


def feats_1x2(df: pd.DataFrame, market: bool = True, sharp: bool = False) -> np.ndarray:
    cols = []
    if market:
        cols += [lg(df[f"mkt_avg_{k}"]) for k in OUT3]
    if sharp:
        cols += [lg(df[f"mkt_sharp_{k}"]) for k in OUT3]
    cols += [lg(df[f"goals_p_{k}"]) for k in OUT3]
    cols += [lg(df[f"pxg_p_{k}"]) for k in OUT3]
    cols += [df["elo_diff"].to_numpy(), np.log1p(df["n_home"]), np.log1p(df["n_away"])]
    return np.column_stack(cols)


def feats_ou(df: pd.DataFrame, market: bool = True) -> np.ndarray:
    cols = []
    if market:
        cols.append(lg(df["mkt_avg_o25"]) - lg(1 - df["mkt_avg_o25"]))
    cols += [lg(df["goals_p_o25"]) - lg(1 - df["goals_p_o25"]), lg(df["pxg_p_o25"]) - lg(1 - df["pxg_p_o25"]),
             np.log(df["goals_lh"] + df["goals_la"]), np.log(df["pxg_lh"] + df["pxg_la"])]
    return np.column_stack(cols)


def feats_btts(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([lg(df["goals_p_btts"]) - lg(1 - df["goals_p_btts"]), lg(df["pxg_p_btts"]) - lg(1 - df["pxg_p_btts"]),
                            np.log(df["pxg_lh"]), np.log(df["pxg_la"])])


def y_1x2(df):
    return np.where(df["hg"] > df["ag"], 0, np.where(df["hg"] == df["ag"], 1, 2))


# ---------- metrikker ----------
def logloss3(P, y):
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], EPS, 1))))


def brier3(P, y):
    Y = np.eye(3)[y]
    return float(np.mean(np.sum((P - Y) ** 2, axis=1)))


def rps3(P, y):
    Y = np.eye(3)[y]
    c = np.cumsum(P, 1)[:, :2] - np.cumsum(Y, 1)[:, :2]
    return float(np.mean(np.sum(c ** 2, 1) / 2))


def logloss2(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier2(p, y):
    return float(np.mean((p - y) ** 2))


# ---------- modeller ----------
def make_lr():
    return LogisticRegression(C=0.5, max_iter=2000)


def make_lgb(n_class):
    return lgb.LGBMClassifier(n_estimators=250, learning_rate=0.03, num_leaves=7, min_child_samples=80,
                              subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=5.0,
                              objective="multiclass" if n_class > 2 else "binary", verbose=-1)


def model_specs() -> dict:
    specs = {
        "stack_lr": ("1x2", lambda d: feats_1x2(d, True), make_lr),
        "own_lr": ("1x2", lambda d: feats_1x2(d, False), make_lr),
        "stack_sharp_lr": ("1x2", lambda d: feats_1x2(d, True, True), make_lr),
        "elo_lr": ("1x2", lambda d: d[["elo_diff"]].to_numpy(), make_lr),
        "ou_stack_lr": ("ou", lambda d: feats_ou(d, True), make_lr),
        "ou_own_lr": ("ou", lambda d: feats_ou(d, False), make_lr),
        "btts_lr": ("btts", feats_btts, make_lr),
    }
    if lgb is not None:
        specs["stack_lgbm"] = ("1x2", lambda d: feats_1x2(d, True), lambda: make_lgb(3))
        specs["ou_stack_lgbm"] = ("ou", lambda d: feats_ou(d, True), lambda: make_lgb(2))
    return specs


def targets_for(kind: str, d: pd.DataFrame) -> np.ndarray:
    if kind == "1x2":
        return y_1x2(d)
    if kind == "ou":
        return ((d["hg"] + d["ag"]) > 2.5).astype(int).to_numpy()
    return ((d["hg"] > 0) & (d["ag"] > 0)).astype(int).to_numpy()


def fit_final(df: pd.DataFrame) -> dict:
    """Tren hver stacking-modell på all historikk; brukes av predict.py til kommende kamper."""
    fitted = {}
    for name, (kind, fx, mk) in model_specs().items():
        X, y = fx(df), targets_for(kind, df)
        ok = np.isfinite(X).all(1)
        mdl = mk()
        mdl.fit(X[ok], y[ok])
        fitted[name] = (kind, mdl)
    return fitted


def walk_forward(df: pd.DataFrame, first_test: str = "1819") -> pd.DataFrame:
    """Legger til OOS-sannsynligheter for alle stacking-modeller, sesong for sesong."""
    df = df.copy()
    seasons = sorted(df["season"].unique())
    specs = model_specs()
    for name, (kind, fx, mk) in specs.items():
        cols = [f"{name}_{k}" for k in OUT3] if kind == "1x2" else [f"{name}_p"]
        for c in cols:
            df[c] = np.nan
        for s in seasons:
            if s < first_test:
                continue
            tr, te = df[df["season"] < s], df[df["season"] == s]
            y = targets_for(kind, tr)
            Xtr, Xte = fx(tr), fx(te)
            mtr, mte = np.isfinite(Xtr).all(1), np.isfinite(Xte).all(1)
            if mte.sum() == 0 or mtr.sum() < 500:
                continue
            model = mk()
            model.fit(Xtr[mtr], y[mtr])
            P = model.predict_proba(Xte[mte])
            if kind == "1x2":
                df.loc[te.index[mte], cols] = P
            else:
                df.loc[te.index[mte], cols[0]] = P[:, 1]
    return df


def probs(df, name):
    if name.startswith("mkt_") or name in ("goals", "pxg"):
        pre = name if name.startswith("mkt_") else f"{name}_p"
        return np.column_stack([df[f"{pre}_{k}"] for k in OUT3])
    return np.column_stack([df[f"{name}_{k}"] for k in OUT3])


def p_over(df, name):
    return {"mkt_avg": df["mkt_avg_o25"], "mkt_sharp": df["mkt_sharp_o25"], "mkt_sharpc": df["mkt_sharpc_o25"], "goals": df["goals_p_o25"],
            "pxg": df["pxg_p_o25"]}.get(name, df.get(f"{name}_p")).to_numpy(float)


# ---------- betting ----------
def simulate(df: pd.DataFrame, P: np.ndarray, kind: str, edge_min: float, nt: float, ret_rows: bool = False) -> dict:
    """Flat 1 enhet per bet der edge > edge_min, mot NT-proxy-odds (snittodds * nt). CLV mot skarp sluttodds."""
    if kind == "1x2":
        # NT-proxy: Bet365 (lå likt med NT mot skarp pris i nov 2026), ellers snittodds
        odds = np.column_stack([df[f"b365_{k}"].fillna(df[f"avg_{k}"]) for k in OUT3]) * nt
        fair_close = np.column_stack([df[f"mkt_sharpc_{k}"] for k in OUT3])
        y = y_1x2(df)
        win = np.eye(3)[y]
    else:
        odds = np.column_stack([df["b365_o25"].fillna(df["avg_o25"]), df["b365_u25"].fillna(df["avg_u25"])]) * nt
        pc = df["mkt_sharpc_o25"].to_numpy(float)
        fair_close = np.column_stack([pc, 1 - pc])
        o = ((df["hg"] + df["ag"]) > 2.5).to_numpy()
        win = np.column_stack([o, ~o]).astype(float)
    edge = P * odds - 1
    edge[~np.isfinite(edge)] = -1
    best = np.argmax(edge, 1)
    e = edge[np.arange(len(df)), best]
    sel = e > edge_min
    o_sel = odds[np.arange(len(df)), best][sel]
    w_sel = win[np.arange(len(df)), best][sel]
    fc = fair_close[np.arange(len(df)), best][sel]
    pnl = np.where(w_sel == 1, o_sel - 1, -1.0)
    clv = o_sel * fc - 1  # odds vi tok / rettferdig sluttodds - 1
    n = int(sel.sum())
    res = {"n": n, "roi": float(pnl.mean()) if n else None, "hit": float(w_sel.mean()) if n else None,
           "clv": float(np.nanmean(clv)) if n else None, "avg_odds": float(o_sel.mean()) if n else None}
    if n >= 30:  # bootstrap 90 % KI for ROI
        rng = np.random.default_rng(7)
        bs = rng.choice(pnl, (2000, n)).mean(1)
        res["roi_lo"], res["roi_hi"] = float(np.quantile(bs, 0.05)), float(np.quantile(bs, 0.95))
    # Kelly-bankroll gjennom tiden (¼ Kelly, samme regler som Oddsboka)
    p_sel = P[np.arange(len(df)), best][sel]
    dates = df["date"].to_numpy()[sel]
    rows = pd.DataFrame({"date": pd.to_datetime(dates), "p": p_sel, "odds": o_sel, "win": w_sel, "edge": e[sel]})
    if n:
        kb = run_bankroll(rows, "kelly_25")
        res["kelly_final"], res["kelly_path"], res["max_dd"] = kb["final"], kb["path"], kb["max_dd"]
    else:
        res["kelly_final"] = 100.0
    if ret_rows:
        res["_rows"] = pd.DataFrame({"date": pd.to_datetime(dates), "league": df["league"].to_numpy()[sel], "p": p_sel,
                                     "odds": o_sel, "win": w_sel, "edge": e[sel], "clv": clv})
    return res


STAKING = {
    "flat_1": "Flat 1 % av bankroll",
    "trapp": "Trapp: 1 % ved 2–4 % edge, 2 % ved 4–6 %, 3 % over 6 %",
    "prop": "Proporsjonal: innsats = edge × 30 %",
    "kelly_25": "¼ Kelly",
    "kelly_33": "⅓ Kelly",
    "kelly_50": "½ Kelly",
    "kelly_100": "Full Kelly",
    "kelly_edge": "Edge-vektet Kelly: ¼ ved 2–4 % edge, ½ ved 4–6 %, ¾ over 6 %",
    "kelly_conf": "Tillitsvektet Kelly: andel = edge × 10 (2 % gir 0,2, 5 % gir 0,5, maks 1)",
    "big_edge": "Bare store edges: ½ Kelly når edge ≥ 5 %, ellers ingenting",
    "odds_band": "Odds-bånd: ½ Kelly bare på odds 1,5–3,5",
}


# Samme regler som maskinlesbar oppskrift, slik at morgenkjøringen kan følge den som er valgt.
# kelly_mult: [[edge_fra, andel av full Kelly], ...] (høyeste terskel ≤ edge gjelder). fixed: fast andel av bankroll.
STAKING_SPEC = {
    "flat_1": {"fixed": 0.01},
    "trapp": {"fixed_tiers": [[0.0, 0.01], [0.04, 0.02], [0.06, 0.03]]},
    "prop": {"edge_mult": 0.3},
    "kelly_25": {"kelly_mult": [[0.0, 0.25]]},
    "kelly_33": {"kelly_mult": [[0.0, 0.33]]},
    "kelly_50": {"kelly_mult": [[0.0, 0.5]]},
    "kelly_100": {"kelly_mult": [[0.0, 1.0]]},
    "kelly_edge": {"kelly_mult": [[0.0, 0.25], [0.04, 0.5], [0.06, 0.75]]},
    "kelly_conf": {"kelly_mult_per_edge": 10, "kelly_mult_max": 1.0},
    "big_edge": {"kelly_mult": [[0.05, 0.5]]},
    "odds_band": {"kelly_mult": [[0.0, 0.5]], "odds_min": 1.5, "odds_max": 3.5},
}


def stake_frac(scheme: str, p: float, o: float, edge: float) -> float:
    """Andel av bankrollen. k = full Kelly = edge / (odds − 1)."""
    k = max((p * o - 1) / (o - 1), 0.0)
    if scheme == "flat_1":
        return 0.01
    if scheme == "trapp":
        return 0.01 if edge < 0.04 else 0.02 if edge < 0.06 else 0.03
    if scheme == "prop":
        return 0.3 * edge
    if scheme.startswith("kelly_") and scheme[6:].isdigit():
        return int(scheme[6:]) / 100 * k
    if scheme == "kelly_edge":
        return (0.25 if edge < 0.04 else 0.5 if edge < 0.06 else 0.75) * k
    if scheme == "kelly_conf":
        return min(1.0, 10 * edge) * k
    if scheme == "big_edge":
        return 0.5 * k if edge >= 0.05 else 0.0
    if scheme == "odds_band":
        return 0.5 * k if 1.5 <= o <= 3.5 else 0.0
    raise ValueError(scheme)


def run_bankroll(rows: pd.DataFrame, scheme: str) -> dict:
    """Bankroll fra $100. Alle dagens bets plasseres mot bankrollen ved dagens start (som i morgenkjøringen).
    Tak: 5 % per bet; overstiger dagens sum 10 %, skaleres alle dagens innsatser ned proporsjonalt."""
    bank, peak, mdd, logs, path = 100.0, 100.0, 0.0, [], []
    for d, g in rows.groupby(rows["date"].dt.normalize(), sort=True):
        f = np.array([min(stake_frac(scheme, p, o, e), CFG["max_stake"]) for p, o, e in zip(g["p"], g["odds"], g["edge"])])
        tot = f.sum()
        if tot > CFG["max_day"]:
            f *= CFG["max_day"] / tot
        ret = float(np.sum(f * np.where(g["win"].to_numpy() == 1, g["odds"].to_numpy() - 1, -1.0)))
        before = bank
        bank = bank * (1 + ret)
        logs.append(np.log(bank / before) if bank > 0 else -10)
        peak = max(peak, bank)
        mdd = max(mdd, 1 - bank / peak)
        path.append((d, bank))
    s = pd.Series({d: b for d, b in path})
    wk = s.resample("W").last().dropna() if len(s) else s
    return {"final": round(bank, 2), "max_dd": round(mdd, 4), "n": int(len(rows)), "days": len(path),
            "growth_per_day": float(np.mean(logs)) if logs else 0.0,
            "path": [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in wk.items()]}


def staking_experiments(rows: pd.DataFrame) -> dict:
    """Samme bets, ulike innsatsregler."""
    return {sc: {"label": label, **run_bankroll(rows, sc)} for sc, label in STAKING.items()}


def rule_breakdown(rows: pd.DataFrame) -> dict:
    by = {}
    for lg, g in rows.groupby("league"):
        pnl = np.where(g["win"] == 1, g["odds"] - 1, -1.0)
        by[lg] = {"n": int(len(g)), "roi": float(pnl.mean()), "clv": float(np.nanmean(g["clv"]))}
    by_year = {}
    for y, g in rows.groupby(rows["date"].dt.year):
        pnl = np.where(g["win"] == 1, g["odds"] - 1, -1.0)
        by_year[str(y)] = {"n": int(len(g)), "roi": float(pnl.mean()), "clv": float(np.nanmean(g["clv"]))}
    return {"by_league": by, "by_year": by_year}


def evaluate(df: pd.DataFrame, first_test="1819") -> dict:
    ev = df[(df["season"] >= first_test)].dropna(subset=["stack_lr_h", "mkt_avg_h", "goals_p_h", "pxg_p_h"])
    y = y_1x2(ev)
    seasons = sorted(ev["season"].unique())
    nt, emin = CFG["nt_odds_factor"], CFG["edge_min"]
    out = {"n_matches": int(len(ev)), "seasons": seasons, "first_test": first_test, "models": {}}
    names = ["mkt_avg", "mkt_sharp", "goals", "pxg", "elo_lr", "own_lr", "stack_lr", "stack_sharp_lr"] + (["stack_lgbm"] if "stack_lgbm_h" in ev else [])
    ref = probs(ev, "mkt_avg")
    for nm in names + ["mkt_sharpc"]:
        P = probs(ev, nm)
        ok = np.isfinite(P).all(1)
        r = {"market": "1x2", "logloss": logloss3(P[ok], y[ok]), "brier": brier3(P[ok], y[ok]), "rps": rps3(P[ok], y[ok]),
             "logloss_vs_mkt": logloss3(P[ok], y[ok]) - logloss3(ref[ok], y[ok]), "by_season": {}}
        for s in seasons:
            m = (ev["season"] == s).to_numpy() & ok
            r["by_season"][s] = {"logloss": logloss3(P[m], y[m]), "mkt": logloss3(ref[m], y[m]), "n": int(m.sum())}
        if nm not in ("mkt_avg", "mkt_sharpc"):
            r["bet"] = simulate(ev[ok], P[ok], "1x2", emin, nt)
            r["bet_by_edge"] = {str(t): {k: v for k, v in simulate(ev[ok], P[ok], "1x2", t, nt).items() if k != "kelly_path"}
                                for t in (0.0, 0.02, 0.04, 0.06, 0.08, 0.10)}
            # kalibrering: 10 bøtter på alle tre utfall
            pp, yy = P[ok].ravel(), np.eye(3)[y[ok]].ravel()
            bins = np.clip((pp * 10).astype(int), 0, 9)
            r["calibration"] = [[float(pp[bins == b].mean()), float(yy[bins == b].mean()), int((bins == b).sum())]
                                for b in range(10) if (bins == b).sum() > 30]
        out["models"][nm] = r
    # Spillregel-analyse: per liga, per år og innsatseksperimenter (for alle modeller med bets)
    out["rule"] = {}
    for nm in ("mkt_sharp", "stack_sharp_lr"):
        P = probs(ev, nm)
        ok = np.isfinite(P).all(1)
        rr = simulate(ev[ok], P[ok], "1x2", emin, nt, ret_rows=True)["_rows"]
        if len(rr):
            out["rule"][nm] = {**rule_breakdown(rr), "staking": staking_experiments(rr)}
    # Over/under 2,5
    evo = ev.dropna(subset=["mkt_avg_o25", "ou_stack_lr_p"])
    yo = ((evo["hg"] + evo["ag"]) > 2.5).astype(int).to_numpy()
    refo = p_over(evo, "mkt_avg")
    for nm in ["mkt_avg", "mkt_sharp", "goals", "pxg", "ou_own_lr", "ou_stack_lr"] + (["ou_stack_lgbm"] if "ou_stack_lgbm_p" in evo else []):
        p = p_over(evo, nm)
        ok = np.isfinite(p)
        r = {"market": "ou25", "logloss": logloss2(p[ok], yo[ok]), "brier": brier2(p[ok], yo[ok]),
             "logloss_vs_mkt": logloss2(p[ok], yo[ok]) - logloss2(refo[ok], yo[ok])}
        if nm != "mkt_avg":
            P2 = np.column_stack([p[ok], 1 - p[ok]])
            r["bet"] = simulate(evo[ok], P2, "ou", emin, nt)
            r["bet_by_edge"] = {str(t): {k: v for k, v in simulate(evo[ok], P2, "ou", t, nt).items() if k != "kelly_path"}
                                for t in (0.0, 0.02, 0.04, 0.06, 0.08)}
        out["models"]["ou:" + nm] = r
    # BTTS (ingen markedsodds i datasettet: bare treffsikkerhet)
    evb = ev.dropna(subset=["btts_lr_p"])
    yb = ((evb["hg"] > 0) & (evb["ag"] > 0)).astype(int).to_numpy()
    base_rate = np.full(len(yb), yb.mean())
    for nm, p in [("basisrate", base_rate), ("goals", evb["goals_p_btts"].to_numpy()), ("pxg", evb["pxg_p_btts"].to_numpy()),
                  ("btts_lr", evb["btts_lr_p"].to_numpy())]:
        out["models"]["btts:" + nm] = {"market": "btts", "logloss": logloss2(p, yb), "brier": brier2(p, yb)}
    return out


def choose_champion(met: dict) -> dict:
    """To valg, begge datadrevne:
    1) Sannsynlighetsmester = lavest log loss siste 3 sesonger (best kalibrert).
    2) Spillregel = modell + edge-terskel med størst samlet CLV (snitt CLV x antall bets), krav CLV > 0 og n >= 150.
       CLV mot skarp sluttodds er et mye sikrere signal enn ROI, som er støyete over få hundre bets.
    Finnes ingen regel med positiv CLV, er riktig svar: ikke spill (bare logg)."""
    last3 = met["seasons"][-3:]
    cand = {k: v for k, v in met["models"].items() if v.get("market") == "1x2" and k != "mkt_sharpc"}
    score = {k: float(np.mean([v["by_season"][s]["logloss"] for s in last3])) for k, v in cand.items()}
    prob = min(score, key=score.get)
    rules = []
    for k, v in met["models"].items():
        for t, b in (v.get("bet_by_edge") or {}).items():
            if b["n"] >= 150 and b["clv"] is not None and b["clv"] > 0:
                rules.append({"model": k, "edge_min": float(t), "clv": b["clv"], "n": b["n"], "roi": b["roi"],
                              "value": b["clv"] * b["n"]})
    rules.sort(key=lambda r: -r["value"])
    best = rules[0] if rules else None
    staking, leagues, dropped = "kelly_25", None, []
    ra = met.get("rule", {}).get(best["model"]) if best else None
    if ra:
        # Innsatsregel: høyest vekst per dag (log) med maks drawdown under 25 %
        ok = {k: v for k, v in ra["staking"].items() if v["max_dd"] < 0.25}
        if ok:
            staking = max(ok, key=lambda k: ok[k]["growth_per_day"])
        # Ligafilter: behold ligaer med positiv CLV, eller for få bets til å dømme (< 30)
        leagues = sorted(lg for lg, v in ra["by_league"].items() if v["clv"] > 0 or v["n"] < 30)
        dropped = sorted(lg for lg, v in ra["by_league"].items() if lg not in leagues)
    return {"prob_model": prob, "logloss_last3": score, "bet_rule": best, "alternatives": rules[1:5], "bet": bool(rules),
            "staking": staking, "staking_label": STAKING[staking], "staking_spec": STAKING_SPEC[staking],
            "leagues": leagues, "leagues_dropped": dropped}


def main():
    df = pd.read_csv(ROOT / "out" / "base_preds.csv", parse_dates=["date"])
    df["season"] = df["season"].astype(str).str.zfill(4)
    df = walk_forward(df)
    df.to_csv(ROOT / "out" / "oos_preds.csv", index=False)
    met = evaluate(df)
    met["champion"] = choose_champion(met)
    import pickle
    with open(ROOT / "out" / "models.pkl", "wb") as fh:
        pickle.dump(fit_final(df), fh)
    (ROOT / "out" / "champion.json").write_text(json.dumps(met["champion"], indent=1))
    met["config"] = CFG
    met["generated"] = pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds")
    (ROOT / "out" / "metrics.json").write_text(json.dumps(met, indent=1, default=float))
    # kort oppsummering
    print(f"{met['n_matches']} kamper testet ({met['seasons'][0]}–{met['seasons'][-1]})")
    for k, v in met["models"].items():
        b = v.get("bet") or {}
        print(f"{k:22s} logloss {v['logloss']:.4f}  vs marked {v.get('logloss_vs_mkt', float('nan')):+.4f}  "
              f"bets {b.get('n', '-')}  ROI {b.get('roi') if b.get('roi') is None else round(b['roi'] * 100, 1)}%  "
              f"CLV {b.get('clv') if b.get('clv') is None else round(b['clv'] * 100, 1)}%  Kelly ${b.get('kelly_final', '-')}")
    print("Mester:", met["champion"])


if __name__ == "__main__":
    main()
