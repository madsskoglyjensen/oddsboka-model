"""Lager out/model_viz.json: innmaten i modellene, for ML-laben i dashboardet.

Alt kommer fra de ekte trente modellene i out/models.pkl og dagens kamper i out/viz_fixtures.json:
- logistisk regresjon: vektene
- random forest: de øverste nivåene i ett av trærne, og hvordan alle trærne stemmer per kamp
- LightGBM: hvordan sannsynligheten bygges opp tre for tre per kamp
- nevralt nettverk: alle vekter, så dashboardet kan regne fremoverpasset selv
- Elo: ratingkurver for topplagene
- feature-viktighet: hvor mye dårligere hver modell blir når en gruppe feature-er stokkes om

Fila flettes inn i out/dashboard.json (feltet viz), så morgenkjøringen synker den til dashboardet.
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
from models import elo_ratings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config.json").read_text())
OUT3 = ("h", "d", "a")
HUB = "HUB"

F12 = [("Snittodds", "h"), ("Snittodds", "d"), ("Snittodds", "a"),
       ("Dixon-Coles mål", "h"), ("Dixon-Coles mål", "d"), ("Dixon-Coles mål", "a"),
       ("Dixon-Coles xG", "h"), ("Dixon-Coles xG", "d"), ("Dixon-Coles xG", "a"),
       ("Elo-forskjell", None), ("Kamper hjemmelag", None), ("Kamper bortelag", None)]
F15 = F12[:3] + [("Skarp pris", "h"), ("Skarp pris", "d"), ("Skarp pris", "a")] + F12[3:]
KIND = {"Elo-forskjell": "elo", "Kamper hjemmelag": "log1p", "Kamper bortelag": "log1p"}  # resten er log-sannsynlighet


def names(F):
    lab = {"h": "hjemme", "d": "uavgjort", "a": "borte"}
    return [{"group": g, "label": g + (f" {lab[o]}" if o else ""), "kind": KIND.get(g, "logp")} for g, o in F]


def r(x, d=4):
    return round(float(x), d)


def tree_top(est, depth: int = 3) -> list[dict]:
    t = est.tree_
    nodes = []

    def walk(i, lvl):
        v = t.value[i][0]
        v = v / v.sum() if v.sum() else v
        node = {"id": int(i), "n": int(t.n_node_samples[i]), "p": [r(x, 3) for x in v], "lvl": lvl}
        if t.children_left[i] != -1 and lvl < depth:
            node.update({"f": int(t.feature[i]), "thr": r(t.threshold[i]), "l": int(t.children_left[i]), "r": int(t.children_right[i])})
            nodes.append(node)
            walk(t.children_left[i], lvl + 1)
            walk(t.children_right[i], lvl + 1)
        else:
            node["leaf"] = bool(t.children_left[i] == -1)
            nodes.append(node)

    walk(0, 0)
    return nodes


def elo_history(m: pd.DataFrame, top: int = 6) -> dict:
    e = elo_ratings(m, k=CFG["elo_k"], hfa=CFG["elo_hfa"])
    m = m.assign(eh=e["elo_home"], ea=e["elo_away"])
    out = {}
    for code in ("E0", "SP1", "I1", "D1", "F1"):
        g = m[m["league"] == code]
        if g.empty:
            continue
        seasons = sorted(g["season"].unique())[-2:]
        g = g[g["season"].isin(seasons)]
        cur = g[g["season"] == seasons[-1]]
        last = {}
        for row in g.itertuples(index=False):
            last[row.home], last[row.away] = row.eh, row.ea
        teams = sorted(set(cur["home"]), key=lambda t: -last.get(t, 0))[:top]
        ser = {}
        for t in teams:
            h = g[g["home"] == t][["date", "eh"]].rename(columns={"eh": "v"})
            a = g[g["away"] == t][["date", "ea"]].rename(columns={"ea": "v"})
            s = pd.concat([h, a]).sort_values("date")
            ser[t] = [[d.strftime("%Y-%m-%d"), int(round(v))] for d, v in zip(s["date"], s["v"])]
        out[data.LEAGUES[code]] = ser
    return out


def importance(fitted: dict) -> dict | None:
    """Gruppevis permutasjonsviktighet: økning i log loss når en feature-gruppe stokkes (siste hele sesong)."""
    bp = ROOT / "out" / "base_preds.csv"
    if not bp.exists():
        return None
    from learn import feats_1x2, y_1x2
    df = pd.read_csv(bp, low_memory=False)
    last = sorted(df["season"].astype(str).unique())[-2]  # siste hele sesong
    d = df[df["season"].astype(str) == last]
    y = y_1x2(d)
    rng = np.random.default_rng(7)
    res = {"season": last, "n": int(len(d)), "models": {}}
    for name, F, sharp in (("stack_lr", F12, False), ("stack_lgbm", F12, False), ("stack_rf", F15, True), ("stack_mlp", F15, True)):
        if name not in fitted:
            continue
        X = feats_1x2(d, True, sharp).astype(float)
        ok = np.isfinite(X).all(1)
        X, yy = X[ok], y[ok]
        mdl = fitted[name][1]

        def ll(Xm):
            P = np.clip(mdl.predict_proba(Xm), 1e-12, 1)
            return float(-np.mean(np.log(P[np.arange(len(yy)), yy])))

        base = ll(X)
        groups = {}
        for j, (g, _) in enumerate(F):
            groups.setdefault(g, []).append(j)
        imp = {}
        for g, cols in groups.items():
            Xp = X.copy()
            perm = rng.permutation(len(Xp))
            Xp[:, cols] = Xp[perm][:, cols]
            imp[g] = r(ll(Xp) - base, 5)
        res["models"][name] = imp
    return res


def main():
    with open(ROOT / "out" / "models.pkl", "rb") as fh:
        fitted = pickle.load(fh)
    fxp = ROOT / "out" / "viz_fixtures.json"
    fixtures = json.loads(fxp.read_text()) if fxp.exists() else []
    today = pd.Timestamp.now(tz="Europe/Oslo").strftime("%Y-%m-%d")
    fixtures = [f for f in fixtures if f["date"] >= today][:24]
    prev = ROOT / "out" / "model_viz.json"
    prev = json.loads(prev.read_text()) if prev.exists() else {}

    lr = fitted["stack_lr"][1]
    rf = fitted["stack_rf"][1]
    mlp_pipe = fitted["stack_mlp"][1]
    sc, mlp = mlp_pipe.named_steps["standardscaler"], mlp_pipe.named_steps["mlpclassifier"]
    lgbm = fitted.get("stack_lgbm", (None, None))[1]
    iters = [k for k in (1, 2, 3, 5, 8, 12, 18, 25, 35, 50, 70, 95, 125, 160, 200, 250) if lgbm is None or k <= lgbm.n_estimators_]

    for f in fixtures:
        x12, x15 = f["x"].get("stack_lr"), f["x"].get("stack_sharp_lr")
        if x15:
            X = np.array([x15])
            f["rf_votes"] = "".join(HUB[int(np.argmax(t.predict_proba(X)[0]))] for t in rf.estimators_)
        if x12 and lgbm is not None:
            X = np.array([x12])
            f["boost"] = [[k] + [r(v, 4) for v in lgbm.predict_proba(X, num_iteration=k)[0]] for k in iters]

    m = data.load_matches()
    imp = importance(fitted) or prev.get("importance")
    viz = {
        "generated": pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds"),
        "features12": names(F12), "features15": names(F15),
        "lr": {"coef": [[r(v) for v in row] for row in lr.coef_], "intercept": [r(v) for v in lr.intercept_], "C": lr.C},
        "rf": {"n_trees": len(rf.estimators_), "max_depth": rf.max_depth, "min_leaf": rf.min_samples_leaf,
               "tree": tree_top(rf.estimators_[0], 3), "tree_depth": int(rf.estimators_[0].tree_.max_depth),
               "tree_leaves": int(rf.estimators_[0].tree_.n_leaves)},
        "mlp": {"mean": [r(v) for v in sc.mean_], "scale": [r(v) for v in sc.scale_],
                "W": [[[r(v, 3) for v in row] for row in W] for W in mlp.coefs_],
                "b": [[r(v, 3) for v in b] for b in mlp.intercepts_], "layers": [int(sc.mean_.shape[0]), *mlp.hidden_layer_sizes, 3],
                "epochs": int(mlp.n_iter_), "activation": mlp.activation},
        "lgbm": {"iters": iters, "n_trees": int(lgbm.n_estimators_) if lgbm is not None else None,
                 "leaves": lgbm.get_params().get("num_leaves") if lgbm is not None else None,
                 "lr": lgbm.get_params().get("learning_rate") if lgbm is not None else None},
        "elo": elo_history(m),
        "importance": imp,
        "pipeline": {"matches": int(len(m)), "leagues": int(m["league"].nunique()), "seasons": int(m["season"].nunique()),
                     "first": str(m["date"].min())[:10], "teams": int(pd.concat([m["home"], m["away"]]).nunique())},
        "fixtures": fixtures,
    }
    (ROOT / "out" / "model_viz.json").write_text(json.dumps(viz, ensure_ascii=False))
    dp = ROOT / "out" / "dashboard.json"
    if dp.exists():
        doc = json.loads(dp.read_text())
        doc.setdefault("trained", doc.get("generated"))
        doc["viz"] = viz
        doc["generated"] = viz["generated"]
        dp.write_text(json.dumps(doc, ensure_ascii=False))
    print(f"model_viz.json: {len(json.dumps(viz)) // 1024} kB, {len(fixtures)} kamper, viktighet: {'ja' if imp else 'nei'}")


if __name__ == "__main__":
    main()
