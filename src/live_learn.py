"""Live-læring: lærer av Oddsbokas egne loggede kamper (matches-samlingen i dashboardets database).

To modeller, som trener seg selv når det finnes nok data:
  A) NT-treghet: når ligger NT-oddsen over rettferdig skarp pris? (liga, tid før avspark, oddsnivå, ukedag)
  B) Sluttodds: kan vi spå hvor den skarpe prisen ender? Har modellens p informasjon markedet ikke har ennå?
I tillegg regnes live-kappløpet (samme strategier som i dashboardet) og beskrivende tall som fungerer fra dag én.

Bruk:  python src/live_learn.py matches.json  -> out/live_learning.json
matches.json er en liste med dokumenter slik de ligger i databasen (felt: date, kickoff, league, locked_at,
nt{h,d,a,o,u}, sharp{h,d,a,o,u}, p_model{h,d,a}, p_own{h,d,a}, in_rule, status, hg, ag, closing_sharp{...}).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MIN_MATCHES_A = 150   # kamper med NT og skarp pris før modell A trenes
MIN_MATCHES_B = 150   # ferdigspilte kamper med sluttodds før modell B trenes
RACE = [  # samme som i dashboardet
    ("r_k25", "Regel 2 % · ¼ Kelly", "sharp", 0.02, {"kelly": [[0, .25]]}),
    ("r_k33", "Regel 2 % · ⅓ Kelly", "sharp", 0.02, {"kelly": [[0, .33]]}),
    ("r_band", "Regel 2 % · odds-bånd ½ Kelly", "sharp", 0.02, {"kelly": [[0, .5]], "omin": 1.5, "omax": 3.5}),
    ("r_k50", "Regel 2 % · ½ Kelly", "sharp", 0.02, {"kelly": [[0, .5]]}),
    ("r_k100", "Regel 2 % · full Kelly", "sharp", 0.02, {"kelly": [[0, 1]]}),
    ("r_edge", "Regel 2 % · mer på store edges", "sharp", 0.02, {"kelly": [[0, .25], [.04, .5], [.06, .75]]}),
    ("r_flat", "Regel 2 % · flat 1 %", "sharp", 0.02, {"fixed": .01}),
    ("r_big", "Bare edge ≥ 6 % · ½ Kelly", "sharp", 0.06, {"kelly": [[0, .5]]}),
    ("m_model", "Sannsynlighetsmodellen ≥ 4 % · ¼ Kelly", "model", 0.04, {"kelly": [[0, .25]]}),
    ("m_own", "Egen modell (xG) ≥ 4 % · ¼ Kelly", "own", 0.04, {"kelly": [[0, .25]]}),
    ("ou", "Over/under 2,5 mot skarp pris ≥ 2 %", "ou", 0.02, {"kelly": [[0, .25]]}),
    ("r_mart", "Martingale: $1, doble etter tap", "sharp", 0.02, {"mart": 1}),
]
FUN = {"r_mart"}
NEED = 150          # live-bets en strategi må ha før live-resultatet kan påvirke valget
# race-id <-> innsatsregel i backtesten (learn.STAKING), for regelen NT mot skarp pris med 2 % edge
BT_KEY = {"r_k25": "kelly_25", "r_k33": "kelly_33", "r_k50": "kelly_50", "r_k100": "kelly_100", "r_edge": "kelly_edge",
          "r_flat": "flat_1", "r_band": "odds_band", "r_mart": "martingale"}


def devig(d: dict | None, keys) -> dict | None:
    if not d or any(not isinstance(d.get(k), (int, float)) or d.get(k, 0) <= 1 for k in keys):
        return None
    inv = {k: 1 / d[k] for k in keys}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def frac(st: dict, p: float, o: float, e: float) -> float:
    k = max((p * o - 1) / (o - 1), 0.0)
    if "mart" in st:
        return 1.0
    if "fixed" in st:
        return st["fixed"]
    if "omin" in st and not (st["omin"] <= o <= st["omax"]):
        return 0.0
    m = 0.0
    for t, v in st.get("kelly", []):
        if e >= t:
            m = v
    return m * k


def race(ms: list[dict]) -> list[dict]:
    out = []
    for sid, name, src, thr, st in RACE:
        bets = []
        for m in ms:
            nt = m.get("nt") or {}
            if src == "ou":
                keys, P = ("o", "u"), devig(m.get("sharp"), ("o", "u"))
            else:
                keys = ("h", "d", "a")
                P = devig(m.get("sharp"), keys) if src == "sharp" else m.get("p_model" if src == "model" else "p_own")
                if src == "sharp" and m.get("in_rule") is False:
                    P = None
            if not P:
                continue
            best = None
            for k in keys:
                o, p = nt.get(k), P.get(k)
                if not isinstance(o, (int, float)) or o <= 1 or not p:
                    continue
                e = p * o - 1
                if best is None or e > best[3]:
                    best = (k, o, p, e)
            if not best or best[3] < thr:
                continue
            f = min(frac(st, best[2], best[1], best[3]), 0.05)
            if f <= 0:
                continue
            res = clv = None
            if m.get("status") == "done" and m.get("hg") is not None:
                hg, ag = m["hg"], m["ag"]
                res = {"h": hg > ag, "d": hg == ag, "a": hg < ag, "o": hg + ag > 2.5, "u": hg + ag < 2.5}[best[0]]
                pc = devig(m.get("closing_sharp"), keys)
                if pc:
                    clv = best[1] * pc[best[0]] - 1
            bets.append({"day": str(m.get("date") or m.get("kickoff"))[:10], "ko": str(m.get("kickoff") or m.get("date") or ""),
                         "f": f, "o": best[1], "res": res, "clv": clv})
        bank, turn, pnl, n, opn, clvs, peak, dd, streak = 100.0, 0.0, 0.0, 0, 0, [], 100.0, 0.0, 0
        if "mart" in st:
            for b in sorted(bets, key=lambda b: b["ko"]):
                if b["res"] is None:
                    opn += 1
                    continue
                if bank <= 0.01:
                    continue
                stake = min(bank, 2.0 ** streak)
                n += 1
                turn += stake
                pl = stake * (b["o"] - 1) if b["res"] else -stake
                pnl += pl
                bank += pl
                streak = 0 if b["res"] else streak + 1
                if b["clv"] is not None:
                    clvs.append(b["clv"])
                peak = max(peak, bank)
                dd = max(dd, 1 - bank / peak)
            bets = []
        for day in sorted({b["day"] for b in bets}):
            g = [b for b in bets if b["day"] == day]
            tot = sum(b["f"] for b in g)
            sc = 0.10 / tot if tot > 0.10 else 1.0
            ret = 0.0
            for b in g:
                if b["res"] is None:
                    opn += 1
                    continue
                n += 1
                stake = bank * b["f"] * sc
                turn += stake
                pl = stake * (b["o"] - 1) if b["res"] else -stake
                pnl += pl
                ret += pl
                if b["clv"] is not None:
                    clvs.append(b["clv"])
            bank += ret
            peak = max(peak, bank)
            dd = max(dd, 1 - bank / peak)
        out.append({"id": sid, "name": name, "src": src, "edge_min": thr, "st": st, "fun": sid in FUN, "max_dd": round(dd, 4),
                    "bank": round(bank, 2), "n": n, "open": opn,
                    "roi": round(pnl / turn, 4) if turn else None, "clv": round(float(np.mean(clvs)), 4) if clvs else None,
                    "n_clv": len(clvs)})
    return sorted(out, key=lambda r: -r["bank"])


def spec_of(st: dict) -> dict:
    """Race-oppskrift -> staking_spec slik morgenkjøringen leser den."""
    if "fixed" in st:
        return {"fixed": st["fixed"]}
    spec = {"kelly_mult": st.get("kelly", [[0.0, 0.25]])}
    if "omin" in st:
        spec.update({"odds_min": st["omin"], "odds_max": st["omax"]})
    return spec


def selection(rows: list[dict]) -> dict:
    """Lar live-kappløpet påvirke søndagsvalget når det finnes nok live-bets.

    Bare strategier med NT mot skarp pris kan ta over (de kan følges direkte av morgenkjøringen). En utfordrer tar over når den
    har minst NEED oppgjorte live-bets, positiv live-CLV, fall under 25 % både live og i backtesten, og minst 10 % høyere bankroll
    enn strategien backtesten har valgt. Ellers beholdes backtestens valg."""
    try:
        champ = json.loads((ROOT / "out" / "champion.json").read_text())
        dash = json.loads((ROOT / "out" / "dashboard.json").read_text())
    except Exception:  # noqa: BLE001
        champ, dash = {}, {}
    rule = champ.get("bet_rule") or {}
    inv = {v: k for k, v in BT_KEY.items()}
    cur_id = inv.get(champ.get("staking")) if rule.get("model") == "mkt_sharp" and abs((rule.get("edge_min") or 0) - 0.02) < 1e-9 else None
    se = dash.get("staking_exp") or {}
    by = {r["id"]: r for r in rows}
    cur = by.get(cur_id)
    base = {"need": NEED, "current": cur_id, "current_name": cur["name"] if cur else None}
    if not cur:
        return {**base, "status": "venter", "n": 0, "reason": "Backtestens valg finnes ikke i kappløpet ennå."}
    if cur["n"] < NEED:
        return {**base, "status": "venter", "n": cur["n"],
                "reason": f"{cur['name']} har {cur['n']} av {NEED} oppgjorte live-bets. Til da styrer backtesten alene."}
    cands = []
    for r in rows:
        bt = se.get(BT_KEY.get(r["id"], ""), {})
        if (r["src"] == "sharp" and not r["fun"] and r["n"] >= NEED and r["max_dd"] < 0.25
                and (bt.get("max_dd") is None or bt["max_dd"] < 0.25) and (r["clv"] or 0) > 0):
            cands.append(r)
    best = max(cands, key=lambda r: r["bank"]) if cands else None
    if best and best["id"] != cur["id"] and best["bank"] >= cur["bank"] * 1.10:
        return {**base, "status": "aktiv", "n": cur["n"], "id": best["id"], "name": best["name"], "edge_min": best["edge_min"],
                "staking_spec": spec_of(best["st"]),
                "reason": (f"{best['name']} står på ${best['bank']:.2f} mot ${cur['bank']:.2f} for {cur['name']} etter {best['n']} live-bets, "
                           f"med live-CLV {best['clv'] * 100:+.1f} % og maks fall {best['max_dd'] * 100:.0f} %. Den styrer bettene til neste søndag.").replace(".", ",").replace(", Den", ". Den").rstrip(",") + "."}
    return {**base, "status": "beholder", "n": cur["n"],
            "reason": f"Ingen utfordrer slår {cur['name']} med minst 10 % og lav nok risiko live. Backtestens valg beholdes."}


def outcome_rows(ms: list[dict]) -> pd.DataFrame:
    rows = []
    for m in ms:
        ps = devig(m.get("sharp"), ("h", "d", "a"))
        nt = m.get("nt") or {}
        if not ps:
            continue
        try:
            ko = pd.Timestamp(m["kickoff"])
            lk = pd.Timestamp(m["locked_at"])
            hrs = (ko - lk).total_seconds() / 3600
        except Exception:  # noqa: BLE001
            ko, hrs = None, np.nan
        pc = devig(m.get("closing_sharp"), ("h", "d", "a"))
        for k in ("h", "d", "a"):
            o = nt.get(k)
            if not isinstance(o, (int, float)) or o <= 1:
                continue
            rows.append({"league": m.get("league"), "k": k, "hours": hrs, "weekday": ko.weekday() if ko is not None else None,
                         "p_sharp": ps[k], "sharp_odds": m["sharp"][k], "nt_odds": o, "value": o * ps[k] - 1,
                         "p_model": (m.get("p_model") or {}).get(k), "p_close": pc[k] if pc else None})
    return pd.DataFrame(rows)


def model_a(df: pd.DataFrame, n_matches: int) -> dict:
    """NT-treghet: beskrivende fra start, logistisk regresjon når nok data."""
    if df.empty:
        return {"status": "venter", "n_matches": 0, "need": MIN_MATCHES_A}
    by_league = df.groupby("league")["value"].agg(["mean", "count", lambda v: (v >= 0.02).mean()]).reset_index()
    by_league.columns = ["league", "mean_value", "n", "share_over_2pct"]
    df["odds_band"] = pd.cut(df["sharp_odds"], [1, 1.6, 2.2, 3.2, 5, 100], labels=["<1,6", "1,6–2,2", "2,2–3,2", "3,2–5", ">5"])
    by_odds = df.groupby("odds_band", observed=True)["value"].agg(["mean", "count"]).reset_index()
    res = {"status": "beskrivende" if n_matches < MIN_MATCHES_A else "trent", "n_matches": n_matches, "need": MIN_MATCHES_A,
           "mean_value": float(df["value"].mean()), "share_over_2pct": float((df["value"] >= 0.02).mean()),
           "by_league": by_league.round(4).to_dict("records"),
           "by_odds": [{"band": str(r.odds_band), "mean_value": round(float(r["mean"]), 4), "n": int(r["count"])} for _, r in by_odds.iterrows()]}
    if n_matches >= MIN_MATCHES_A:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import cross_val_score
        X = pd.get_dummies(df[["league", "k"]].astype(str)).assign(
            hours=df["hours"].fillna(df["hours"].median()), logodds=np.log(df["sharp_odds"]), weekday=df["weekday"].fillna(5))
        y = (df["value"] >= 0.02).astype(int)
        if y.nunique() == 2:
            lr = LogisticRegression(max_iter=2000, C=0.5)
            auc = cross_val_score(lr, X, y, cv=5, scoring="roc_auc").mean()
            lr.fit(X, y)
            coefs = sorted(zip(X.columns, lr.coef_[0]), key=lambda t: -abs(t[1]))[:8]
            # Beslutningstre: lesbare regler for når NT er treg
            from sklearn.tree import DecisionTreeClassifier
            Xt = X.copy()
            tree = DecisionTreeClassifier(max_depth=3, min_samples_leaf=max(15, len(y) // 30), random_state=7).fit(Xt, y)
            rules = tree_rules(tree, list(Xt.columns))
            res["tree_rules"] = rules
            res.update({"auc_cv": round(float(auc), 3), "top_features": [[c, round(float(v), 3)] for c, v in coefs],
                        "verdict": "Modellen finner mønster i når NT er treg." if auc > 0.6 else "Ingen tydelig mønster ennå: NT-treghet ser tilfeldig ut."})
    return res


def _feat_text(name: str, thr: float, left: bool) -> str:
    if name.startswith("league_"):
        return ("ikke " if left else "") + name[7:]
    if name.startswith("k_"):
        lab = {"h": "hjemmeseier", "d": "uavgjort", "a": "borteseier"}.get(name[2:], name[2:])
        return ("ikke " if left else "") + lab
    if name == "logodds":
        o = float(np.exp(thr))
        return f"skarp odds {'≤' if left else '>'} {o:.2f}".replace(".", ",")
    if name == "hours":
        return f"{'≤' if left else '>'} {thr:.0f} timer før avspark"
    if name == "weekday":
        return f"ukedag {'≤' if left else '>'} {thr:.0f} (0 = mandag)"
    return f"{name} {'≤' if left else '>'} {thr:.2f}"


def tree_rules(tree, names: list[str]) -> list[dict]:
    """Hvert blad i treet som en regel i klartekst, med andel utfall der NT lå over spillgrensen."""
    t = tree.tree_
    out = []
    unit = {"logodds": ("skarp odds", np.exp, 2), "hours": ("timer før avspark", float, 0), "weekday": ("ukedag (0 = man)", float, 0)}

    def text(cats, bounds):
        parts = list(cats)
        for f, (lo, hi) in bounds.items():
            lab, fn, d = unit.get(f, (f, float, 2))
            fmt = lambda x: f"{fn(x):.{d}f}".replace(".", ",")
            if lo is not None and hi is not None:
                parts.append(f"{lab} {fmt(lo)}–{fmt(hi)}")
            elif lo is not None:
                parts.append(f"{lab} over {fmt(lo)}")
            else:
                parts.append(f"{lab} til {fmt(hi)}")
        return " · ".join(parts) or "alle"

    def walk(node, cats, bounds):
        if t.children_left[node] == -1:
            v = t.value[node][0]
            share = float(v[1] / v.sum()) if v.sum() else 0.0
            out.append({"rule": text(cats, bounds), "share": round(share, 3), "n": int(t.n_node_samples[node])})
            return
        f, thr = names[t.feature[node]], float(t.threshold[node])
        if f.startswith(("league_", "k_")):
            walk(t.children_left[node], cats + [_feat_text(f, thr, True)], bounds)
            walk(t.children_right[node], cats + [_feat_text(f, thr, False)], bounds)
        else:
            lo, hi = bounds.get(f, (None, None))
            walk(t.children_left[node], cats, {**bounds, f: (lo, thr)})
            walk(t.children_right[node], cats, {**bounds, f: (thr, hi)})

    walk(0, [], {})
    return sorted(out, key=lambda r: -r["share"])


def model_b(df: pd.DataFrame) -> dict:
    """Sluttodds: forklarer (p_model − p_sharp) hvor den skarpe prisen flytter seg før avspark?"""
    if df.empty or "p_close" not in df:
        return {"status": "venter", "n_matches": 0, "need": MIN_MATCHES_B}
    d = df.dropna(subset=["p_close", "p_model"])
    n = int(len(d) // 3)
    if n < 10:
        return {"status": "venter", "n_matches": n, "need": MIN_MATCHES_B}
    move = d["p_close"] - d["p_sharp"]            # hvor markedet gikk
    signal = d["p_model"] - d["p_sharp"]          # hva modellen mente om markedet om morgenen
    corr = float(np.corrcoef(signal, move)[0, 1]) if move.std() > 0 and signal.std() > 0 else None
    res = {"status": "beskrivende" if n < MIN_MATCHES_B else "trent", "n_matches": n, "need": MIN_MATCHES_B,
           "corr_model_vs_move": round(corr, 3) if corr is not None else None,
           "mean_abs_move": round(float(move.abs().mean()), 4)}
    if n >= MIN_MATCHES_B:
        from sklearn.linear_model import LinearRegression
        from sklearn.model_selection import cross_val_score
        X = np.column_stack([signal, d["value"]])
        r2 = cross_val_score(LinearRegression(), X, move, cv=5, scoring="r2").mean()
        res.update({"r2_cv": round(float(r2), 3),
                    "verdict": "Modellen ser deler av markedsbevegelsen før den skjer." if r2 > 0.02 else "Modellen spår ikke hvor markedet går."})
    return res


MIN_CAL = 40  # utfall per liga før NT-kalibreringen brukes i backtesten


def nt_calibration(df: pd.DataFrame) -> dict:
    """Ekte NT-nivå per liga: median av NT-odds / skarp odds på samme tidspunkt."""
    if df.empty:
        return {"status": "venter", "leagues": {}, "need_per_league": MIN_CAL}
    d = df.assign(ratio=df["nt_odds"] / df["sharp_odds"])
    d = d[(d["ratio"] > 0.7) & (d["ratio"] < 1.3)]
    lg = {k: {"nt_vs_sharp": round(float(g["ratio"].median()), 4), "n": int(len(g))} for k, g in d.groupby("league")}
    ready = {k: v for k, v in lg.items() if v["n"] >= MIN_CAL}
    return {"status": "klar" if ready else "samler", "leagues": lg, "ready": sorted(ready), "need_per_league": MIN_CAL,
            "all": round(float(d["ratio"].median()), 4) if len(d) else None}


def main(path: str):
    ms = json.loads(Path(path).read_text())
    ms = [m.get("data", m) for m in ms]
    ms = [m for m in ms if m.get("locked_at")]
    df = outcome_rows(ms)
    n_with = int(df.groupby(["league"]).size().sum() // 3) if not df.empty else 0
    out = {"generated": pd.Timestamp.now(tz="Europe/Oslo").isoformat(timespec="seconds"),
           "n_logged": len(ms), "n_done": sum(1 for m in ms if m.get("status") == "done"),
           "race": (rc := race(ms)), "selection": selection(rc), "nt_lag": model_a(df, n_with), "closing_line": model_b(df), "nt_calibration": nt_calibration(df)}
    (ROOT / "out" / "nt_calibration.json").write_text(json.dumps(out["nt_calibration"], ensure_ascii=False, indent=1))
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "live_learning.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    print(f"Live-valg: {out['selection']['status']} ({out['selection']['reason']})")
    print(f"{out['n_logged']} kamper logget, {out['n_done']} ferdige. NT-treghet: {out['nt_lag']['status']}, sluttodds: {out['closing_line']['status']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "data" / "matches_live.json"))
