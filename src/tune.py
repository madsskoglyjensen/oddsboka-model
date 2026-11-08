"""Ukentlig tuning av grunnmodellens parametere (koordinatsøk), evaluert walk-forward på de siste sesongene.

Endrer config.json bare hvis forbedringen i log loss er større enn støyterskelen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from base import base_features  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GRID = {"xi": [0.0010, 0.0019, 0.0030, 0.0045], "shrink": [0.5, 2.0, 5.0, 10.0], "pxg_alpha": [0.2, 0.35, 0.5, 0.7]}
MIN_GAIN = 0.0005  # log loss; mindre enn dette regnes som støy


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
    cfg = json.loads((ROOT / "config.json").read_text())
    m = pd.read_csv(ROOT / "data" / "matches.csv", parse_dates=["date"])
    m["season"] = m["season"].astype(str).str.zfill(4)
    test = sorted(m["season"].unique())[-seasons_back - 1:-1]  # siste hele sesonger
    targets = m[m["season"].isin(test)]
    base = score(m, targets, cfg)
    log = {"start": base, "tested": [], "seasons": test}
    best, best_cfg = base, dict(cfg)
    for key, vals in GRID.items():
        for v in vals:
            if v == best_cfg[key]:
                continue
            c = dict(best_cfg, **{key: v})
            s = score(m, targets, c)
            log["tested"].append({key: v, "logloss": s})
            print(f"{key}={v}: {s:.4f} (beste {best:.4f})", flush=True)
            if s < best:
                best, best_cfg = s, c
    changed = base - best > MIN_GAIN
    log.update({"best": best, "gain": base - best, "changed": changed,
                "params": {k: best_cfg[k] for k in GRID}})
    if changed:
        (ROOT / "config.json").write_text(json.dumps(best_cfg, indent=2))
    (ROOT / "out" / "tuning.json").write_text(json.dumps(log, indent=1))
    print("Endret:" if changed else "Ingen endring:", log["params"], f"gevinst {base - best:.4f}")


if __name__ == "__main__":
    main()
