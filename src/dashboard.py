"""Lager out/dashboard.json: det dashboardet trenger fra den ukentlige læringen.

Morgenkjøringen kopierer fila inn i dashboardets database (model/backtest) når den er ny.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from data import LEAGUES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

NAME = {"mkt_sharp": "Skarp markedspris", "stack_sharp_lr": "Stacking: skarp + snitt + egne", "stack_lr": "Stacking: snitt + egne",
        "stack_lgbm": "LightGBM-stacking", "own_lr": "Egne modeller samlet", "pxg": "Dixon-Coles på xG-proxy",
        "goals": "Dixon-Coles på mål", "elo_lr": "Elo", "mkt_avg": "Snittodds (referanse)",
        "ou:mkt_sharp": "O/U 2,5: skarp pris", "ou:ou_stack_lr": "O/U 2,5: stacking", "ou:pxg": "O/U 2,5: xG-proxy",
        "ou:goals": "O/U 2,5: mål", "btts:btts_lr": "BTTS: modell", "btts:basisrate": "BTTS: basisrate"}
DESC = {"mkt_sharp": "Pinnacle, ellers Betfair, uten margin", "stack_sharp_lr": "Logistisk regresjon på markeder, Dixon-Coles og Elo",
        "stack_lr": "Som over, uten skarp pris", "stack_lgbm": "Gradient boosting på de samme feature-ene",
        "own_lr": "Bare egne modeller, ingen odds", "pxg": "Lagstyrke fra skudd på mål og mål", "goals": "Klassisk lagstyrke fra mål",
        "elo_lr": "Rating med målmargin", "mkt_avg": "Snitt av bookmakere uten margin"}


def r4(x):
    return None if x is None else round(float(x), 4)


def nd(x, d):
    return f"{x:.{d}f}".replace(".", ",")


def pc(x, d=1):
    s = f"{abs(x) * 100:.{d}f}".replace(".", ",")
    return ("+" if x > 0 else "−" if x < 0 else "") + s + " %"


def findings(m: dict, ch: dict, ra: dict, doc: dict) -> list[str]:
    """Funn i klartekst, regnet ut på nytt hver søndag."""
    out = []
    M = m["models"]
    own, mk = M.get("pxg"), M.get("mkt_avg")
    if own and mk:
        b = own.get("bet") or {}
        out.append(f"Egen modell mot markedet: log loss {nd(own['logloss'], 3)} mot {nd(mk['logloss'], 3)} (lavere er bedre). "
                   f"Spilt mot NT-lignende odds gir den {pc(b.get('roi') or 0)} ROI. Markedet vet mer enn kampstatistikken.")
    r = ch.get("bet_rule")
    if r:
        rb = M[r["model"]]["bet"]
        out.append(f"Spillregelen (NT mot skarp pris, edge minst {r['edge_min'] * 100:.0f} %) ga {r['n']} bets i backtesten, "
                   f"slo sluttoddsen med {pc(r['clv'])} i snitt og ga {pc(r['roi'])} ROI. $100 ble ${rb.get('kelly_final', 0):.0f} med ¼ Kelly.")
    se = ra.get("staking") or {}
    if se:
        best_final = max(se, key=lambda k: se[k]["final"])
        chosen = ch.get("staking")
        txt = f"Innsatslab: {se[chosen]['label']} er valgt (${se[chosen]['final']:.0f}, maks fall {se[chosen]['max_dd'] * 100:.0f} %)."
        if best_final != chosen:
            txt += (f" {se[best_final]['label']} endte høyere (${se[best_final]['final']:.0f}), men falt opptil "
                    f"{se[best_final]['max_dd'] * 100:.0f} % underveis, over grensen på 25 %.")
        if "kelly_edge" in se and "kelly_25" in se and se["kelly_edge"]["final"] < se["kelly_25"]["final"]:
            txt += (f" Å satse mer på store edges (edge-vektet Kelly) ga ${se['kelly_edge']['final']:.0f} mot ${se['kelly_25']['final']:.0f} for ¼ Kelly: "
                    "de største edgene ligger oftest på høye odds, der prisforskjellen oftere er støy enn ekte verdi.")
        if "trapp" in se:
            txt += f" Innsats etter edge-trapp ga ${se['trapp']['final']:.0f}: den satser for mye på høye odds. Kelly tar hensyn til både edge og odds."
        out.append(txt)
    lg = doc.get("league_rows") or []
    if lg:
        best, worst = lg[0], lg[-1]
        txt = f"Ligaer: sterkest i {best['name']} ({pc(best['clv'])} CLV), svakest i {worst['name']} ({pc(worst['clv'])})."
        if ch.get("leagues_dropped"):
            txt += " Tatt ut av regelen: " + ", ".join(LEAGUES.get(x, x) for x in ch["leagues_dropped"]) + "."
        out.append(txt)
    by = ra.get("by_year") or {}
    if len(by) >= 3:
        ys = sorted(by)
        last2 = [by[y] for y in ys[-2:]]
        if all(v["clv"] < 0.005 for v in last2):
            out.append(f"Varsel: fordelen har krympet. CLV var {pc(by[ys[-2]]['clv'])} i {ys[-2]} og {pc(by[ys[-1]]['clv'])} i {ys[-1]}, "
                       "mot over 2 % tidligere. Det kan bety at bookmakerne har blitt raskere, men delvis også målingen: fra høsten 2026 mangler "
                       "Pinnacle i datakilden, og Betfair brukes som skarp pris. Live-CLV de neste månedene avgjør.")
    ou = M.get("ou:mkt_sharp", {}).get("bet") or {}
    bt, br = M.get("btts:btts_lr"), M.get("btts:basisrate")
    if ou.get("n") is not None:
        txt = f"Over/under 2,5 mot skarp pris: {ou['n']} bets, CLV {pc(ou.get('clv') or 0)}. For få til å stole på (krav 150), så O/U spilles ikke ennå."
        if bt and br:
            txt += f" BTTS-modellen slår bare basisraten marginalt (log loss {nd(bt['logloss'], 4)} mot {nd(br['logloss'], 4)})."
        out.append(txt)
    return out


def main():
    m = json.loads((ROOT / "out" / "metrics.json").read_text())
    tu = json.loads((ROOT / "out" / "tuning.json").read_text()) if (ROOT / "out" / "tuning.json").exists() else {}
    ch = m["champion"]
    rule = ch.get("bet_rule") or {}
    rows = []
    for k, label in NAME.items():
        v = m["models"].get(k)
        if not v:
            continue
        b = v.get("bet") or {}
        base = k.split(":")[-1]
        rows.append({"id": k, "name": label, "desc": DESC.get(base, ""), "market": v["market"], "logloss": r4(v["logloss"]),
                     "vs_mkt": r4(v.get("logloss_vs_mkt")), "n": b.get("n"), "roi": r4(b.get("roi")), "roi_lo": r4(b.get("roi_lo")),
                     "roi_hi": r4(b.get("roi_hi")), "clv": r4(b.get("clv")), "kelly_final": b.get("kelly_final"),
                     "champion": k == ch["prob_model"], "bet_rule": rule.get("model") == k})
    # Utfordrere: alle varianter av spillregel (modell x edge-terskel), rangert på samlet CLV
    challengers = []
    for k in ("mkt_sharp", "stack_sharp_lr", "stack_lr", "pxg", "ou:mkt_sharp"):
        for t, b in (m["models"].get(k, {}).get("bet_by_edge") or {}).items():
            if not b.get("n"):
                continue
            challengers.append({"model": k, "name": NAME.get(k, k), "edge_min": float(t), "n": b["n"], "roi": r4(b["roi"]),
                                "clv": r4(b["clv"]), "kelly_final": b.get("kelly_final"),
                                "active": k == rule.get("model") and abs(float(t) - rule.get("edge_min", -1)) < 1e-9,
                                "eligible": b["n"] >= 150 and (b["clv"] or 0) > 0})
    challengers.sort(key=lambda c: -((c["clv"] or -1) * c["n"]))
    ra = m.get("rule", {}).get(rule.get("model"), {})
    leagues = [{"code": lg, "name": LEAGUES.get(lg, lg), **{kk: r4(vv) if kk != "n" else vv for kk, vv in v.items()},
                "in_rule": lg in (ch.get("leagues") or [])} for lg, v in (ra.get("by_league") or {}).items()]
    leagues.sort(key=lambda x: -(x["clv"] or 0))
    doc = {
        "generated": m["generated"], "version": m["config"].get("version"), "n_matches": m["n_matches"], "seasons": m["seasons"],
        "first_test": m["first_test"], "prob_model": ch["prob_model"], "bet_rule": rule, "staking": ch.get("staking"),
        "staking_label": ch.get("staking_label"), "leagues_in_rule": ch.get("leagues"), "rows": rows,
        "challengers": challengers[:14], "league_rows": leagues, "by_year": ra.get("by_year"),
        "staking_exp": {k: {kk: vv for kk, vv in v.items()} for k, v in (ra.get("staking") or {}).items()},
        "paths": {k: m["models"][k]["bet"].get("kelly_path", []) for k in ("mkt_sharp", "stack_sharp_lr", "pxg") if k in m["models"]},
        "gap": {k: {s: round(v["logloss"] - v["mkt"], 4) for s, v in m["models"][k]["by_season"].items()}
                for k in ("mkt_sharp", "stack_sharp_lr", "pxg") if k in m["models"]},
        "calibration": {k: m["models"][k]["calibration"] for k in ("stack_sharp_lr", "pxg") if "calibration" in m["models"].get(k, {})},
        "edges": {k: {t: {"n": b["n"], "clv": r4(b["clv"]), "roi": r4(b["roi"]), "kelly_final": b.get("kelly_final")}
                      for t, b in m["models"][k]["bet_by_edge"].items()}
                  for k in ("mkt_sharp", "stack_sharp_lr", "stack_lr", "pxg", "ou:mkt_sharp") if k in m["models"]},
        "tuning": {"params": tu.get("params"), "gain": r4(tu.get("gain")), "changed": tu.get("changed")},
        "nt_proxy": {"book": "Bet365", "factor": m["config"].get("nt_odds_factor"), "nt_vs_sharp": 0.955, "b365_vs_sharp": 0.956, "n_checked": 22},
    }
    doc["findings"] = findings(m, ch, ra, doc)
    (ROOT / "out" / "dashboard.json").write_text(json.dumps(doc, ensure_ascii=False))
    print(f"dashboard.json: {len(json.dumps(doc)) // 1024} kB")


if __name__ == "__main__":
    main()
