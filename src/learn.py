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
def simulate(df: pd.DataFrame, P: np.ndarray, kind: str, edge_min: float, nt: float) -> dict:
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
    # Kelly-bankroll gjennom tiden
    p_sel = P[np.arange(len(df)), best][sel]
    dates = df["date"].to_numpy()[sel]
    bank, path = 100.0, []
    f = CFG["kelly_fraction"] * np.clip((p_sel * o_sel - 1) / (o_sel - 1), 0, None)
    f = np.minimum(f, CFG["max_stake"])
    day_used: dict = {}
    for d, fi, oi, wi in zip(dates, f, o_sel, w_sel):
        dk = str(d)[:10]
        room = CFG["max_day"] - day_used.get(dk, 0)
        fi = max(min(fi, room), 0)
        day_used[dk] = day_used.get(dk, 0) + fi
        stake = bank * fi
        bank += stake * (oi - 1) if wi == 1 else -stake
        path.append((dk, round(bank, 2)))
    res["kelly_final"] = round(bank, 2)
    if path:
        s = pd.Series({d: b for d, b in path})
        s.index = pd.to_datetime(s.index)
        wk = s.resample("W").last().dropna()
        res["kelly_path"] = [[d.strftime("%Y-%m-%d"), float(v)] for d, v in wk.items()]
    return res


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
    return {"prob_model": prob, "logloss_last3": score, "bet_rule": rules[0] if rules else None,
            "alternatives": rules[1:5], "bet": bool(rules)}


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
