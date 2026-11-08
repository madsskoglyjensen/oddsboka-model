"""Ukentlig tuning av grunnmodellens parametere med Optuna (bayesiansk søk), evaluert walk-forward.

Søket starter fra gjeldende parametere og prøver nye kombinasjoner innenfor et tidsbudsjett.
config.json endres bare hvis forbedringen i log loss er større enn støyterskelen.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from base import base_features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MIN_GAIN = 0.0005  # log loss; mindre enn dette regnes som støy
SPACE = {"xi": (0.0005, 0.006, True), "shrink": (0.1, 15.0, True), "pxg_alpha": (0.1, 0.9, False)}


def score(m: pd.DataFrame, targets: pd.DataFrame, cfg: dict) -> float:
    b = base_features(m, targets, cfg)
    t = targets.join(b, how="inner")
    y = np.where(t["hg"] > t["ag"], 0, np.where(t["hg"] == t["ag"], 1, 2))
    lls = []
    for pre in ("goals", "pxg"):
        P = t[[f"{pre}_p_h", f"{pre}_p_d", f"{pre}_p_a"]].to_numpy()
        ok = np.isfinite(P).all(1)
        lls.append(-np.mean(np.log(np.clip(P[ok][np.arange(ok.sum()), y[ok]], 1e-6, 1))))
    return float(min(lls))


def main(seasons_back: int = 3):
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    minutes = float(os.environ.get("TUNE_MINUTES", "8"))
    max_trials = int(os.environ.get("TUNE_TRIALS", "40"))
    cfg = json.loads((ROOT / "config.json").read_text())
    m = pd.read_csv(ROOT / "data" / "matches.csv", parse_dates=["date"])
    m["season"] = m["season"].astype(str).str.zfill(4)
    test = sorted(m["season"].unique())[-seasons_back - 1:-1]  # siste hele sesonger
    targets = m[m["season"].isin(test)]
    t0 = time.time()
    base = score(m, targets, cfg)

    def objective(trial):
        c = dict(cfg)
        for k, (lo, hi, logscale) in SPACE.items():
            c[k] = trial.suggest_float(k, lo, hi, log=logscale)
        return score(m, targets, c)

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=int(time.time()) % 10000))
    study.enqueue_trial({k: cfg[k] for k in SPACE})
    study.optimize(objective, n_trials=max_trials, timeout=minutes * 60)
    best = study.best_value
    params = {k: round(v, 5) for k, v in study.best_params.items()}
    changed = base - best > MIN_GAIN
    if changed:
        cfg.update(params)
        (ROOT / "config.json").write_text(json.dumps(cfg, indent=2))
    log = {"method": "optuna", "seasons": test, "start": base, "best": best, "gain": base - best, "changed": changed,
           "params": {k: cfg[k] for k in SPACE}, "n_trials": len(study.trials), "seconds": round(time.time() - t0),
           "tested": [{**{k: round(v, 5) for k, v in t.params.items()}, "logloss": t.value} for t in study.trials if t.value is not None]}
    (ROOT / "out" / "tuning.json").write_text(json.dumps(log, indent=1))
    print(("Endret: " if changed else "Ingen endring: ") + str(log["params"]) + f" gevinst {base - best:.4f}, {len(study.trials)} forsøk")


if __name__ == "__main__":
    main()
