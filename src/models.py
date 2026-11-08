"""Grunnmodellene: markedet (uten margin), Dixon-Coles med tidsvekting, og Elo.

Dixon-Coles kan trenes på mål ("goals") eller på en skuddbasert xG-proxy ("pxg"),
der skudd på mål gjøres om til forventede mål med ligaens konverteringsrate.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

MAXG = 10


# ---------- marked ----------
def devig(*odds: np.ndarray) -> list[np.ndarray]:
    """Proporsjonal fjerning av margin: p_i = (1/o_i) / sum(1/o_j)."""
    inv = [1.0 / np.asarray(o, dtype=float) for o in odds]
    tot = sum(inv)
    return [i / tot for i in inv]


# ---------- Dixon-Coles ----------
@dataclass
class DCParams:
    teams: dict
    att: np.ndarray
    dfn: np.ndarray
    mu: float
    home: float
    rho: float
    n_games: dict

    def rates(self, home: str, away: str) -> tuple[float, float]:
        i, j = self.teams.get(home), self.teams.get(away)
        ai, di = (self.att[i], self.dfn[i]) if i is not None else (self.promo_att, self.promo_def)
        aj, dj = (self.att[j], self.dfn[j]) if j is not None else (self.promo_att, self.promo_def)
        lh = np.exp(self.mu + self.home + ai - dj)
        la = np.exp(self.mu + aj - di)
        return float(lh), float(la)

    @property
    def promo_att(self) -> float:  # ukjent lag: som de svakeste i ligaen
        return float(np.quantile(self.att, 0.15))

    @property
    def promo_def(self) -> float:
        return float(np.quantile(self.dfn, 0.15))


def _tau(x, y, lh, la, rho):
    t = np.ones_like(lh)
    t = np.where((x == 0) & (y == 0), 1 - lh * la * rho, t)
    t = np.where((x == 0) & (y == 1), 1 + lh * rho, t)
    t = np.where((x == 1) & (y == 0), 1 + la * rho, t)
    t = np.where((x == 1) & (y == 1), 1 - rho, t)
    return np.clip(t, 1e-6, None)


def fit_dc(hist: pd.DataFrame, ref_date: pd.Timestamp, xi: float = 0.0019, target: str = "goals",
           shrink: float = 2.0, pxg_alpha: float = 0.5, window_days: int = 1100) -> DCParams | None:
    """Vektet Poisson-regresjon (Maher/Dixon-Coles) med eksponentiell tidsvekt exp(-xi*dager)."""
    h = hist[(hist["date"] < ref_date) & (hist["date"] >= ref_date - pd.Timedelta(days=window_days))]
    if len(h) < 150:
        return None
    teams = sorted(set(h["home"]) | set(h["away"]))
    idx = {t: k for k, t in enumerate(teams)}
    n = len(teams)
    hi = h["home"].map(idx).to_numpy()
    ai = h["away"].map(idx).to_numpy()
    days = (ref_date - h["date"]).dt.days.to_numpy()
    w = np.exp(-xi * days)
    hg, ag = h["hg"].to_numpy(float), h["ag"].to_numpy(float)
    if target == "pxg":
        sot = h["hst"].to_numpy(float) + h["ast"].to_numpy(float)
        conv = (hg.sum() + ag.sum()) / max(np.nansum(sot), 1)
        yh = pxg_alpha * hg + (1 - pxg_alpha) * np.nan_to_num(h["hst"].to_numpy(float), nan=hg / max(conv, 1e-6)) * conv
        ya = pxg_alpha * ag + (1 - pxg_alpha) * np.nan_to_num(h["ast"].to_numpy(float), nan=ag / max(conv, 1e-6)) * conv
    else:
        yh, ya = hg, ag

    def f(theta):
        att, dfn, mu, home = theta[:n], theta[n:2 * n], theta[2 * n], theta[2 * n + 1]
        llh = mu + home + att[hi] - dfn[ai]
        lla = mu + att[ai] - dfn[hi]
        lh, la = np.exp(llh), np.exp(lla)
        nll = np.sum(w * (lh - yh * llh)) + np.sum(w * (la - ya * lla))
        nll += shrink * (np.sum(att ** 2) + np.sum(dfn ** 2))
        rh, ra = w * (lh - yh), w * (la - ya)
        g = np.zeros_like(theta)
        np.add.at(g, hi, rh)
        np.add.at(g, ai, ra)
        np.add.at(g, n + ai, -rh)
        np.add.at(g, n + hi, -ra)
        g[:n] += 2 * shrink * att
        g[n:2 * n] += 2 * shrink * dfn
        g[2 * n] = rh.sum() + ra.sum()
        g[2 * n + 1] = rh.sum()
        return nll, g

    x0 = np.zeros(2 * n + 2)
    x0[2 * n] = np.log(max((yh.mean() + ya.mean()) / 2, 0.1))
    x0[2 * n + 1] = 0.2
    r = minimize(f, x0, jac=True, method="L-BFGS-B")
    th = r.x
    att, dfn, mu, home = th[:n], th[n:2 * n], th[2 * n], th[2 * n + 1]
    # rho (lavscore-korreksjon) estimeres på faktiske mål, gitt rates
    lh = np.exp(mu + home + att[hi] - dfn[ai])
    la = np.exp(mu + att[ai] - dfn[hi])
    x, y = h["hg"].to_numpy(), h["ag"].to_numpy()
    rr = minimize_scalar(lambda rho: -np.sum(w * np.log(_tau(x, y, lh, la, rho))), bounds=(-0.25, 0.25), method="bounded")
    games = pd.concat([h["home"], h["away"]]).value_counts().to_dict()
    return DCParams(idx, att, dfn, float(mu), float(home), float(rr.x), games)


def score_matrix(lh: float, la: float, rho: float = 0.0) -> np.ndarray:
    g = np.arange(MAXG + 1)
    m = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    m[0, 0] *= 1 - lh * la * rho
    m[0, 1] *= 1 + lh * rho
    m[1, 0] *= 1 + la * rho
    m[1, 1] *= 1 - rho
    m = np.clip(m, 0, None)
    return m / m.sum()


def markets_from_matrix(m: np.ndarray) -> dict:
    tot = np.add.outer(np.arange(m.shape[0]), np.arange(m.shape[1]))
    return {
        "p_h": float(np.tril(m, -1).sum()),
        "p_d": float(np.trace(m)),
        "p_a": float(np.triu(m, 1).sum()),
        "p_o25": float(m[tot > 2.5].sum()),
        "p_btts": float(m[1:, 1:].sum()),
    }


# ---------- Elo ----------
def elo_ratings(matches: pd.DataFrame, k: float = 20.0, hfa: float = 60.0, carry: float = 0.67) -> pd.DataFrame:
    """Elo per liga med målmargin. Returnerer elo_home/elo_away FØR hver kamp (ingen lekkasje)."""
    out = np.full((len(matches), 2), np.nan)
    for lg, g in matches.groupby("league", sort=False):
        r: dict[str, float] = {}
        season = None
        for pos, row in zip(g.index, g.itertuples(index=False)):
            if row.season != season:
                season = row.season
                if r:
                    mean = np.mean(list(r.values()))
                    r = {t: mean + carry * (v - mean) for t, v in r.items()}
                teams_now = set(g.loc[g["season"] == season, "home"])
                low = np.mean(sorted(r.values())[:3]) if len(r) >= 3 else 1500.0
                for t in teams_now:
                    r.setdefault(t, low)
            rh, ra = r[row.home], r[row.away]
            out[pos] = (rh, ra)
            e = 1 / (1 + 10 ** (-(rh + hfa - ra) / 400))
            s = 1.0 if row.hg > row.ag else 0.5 if row.hg == row.ag else 0.0
            mult = np.log(abs(row.hg - row.ag) + 1) + 1
            d = k * mult * (s - e)
            r[row.home] = rh + d
            r[row.away] = ra - d
    return pd.DataFrame(out, index=matches.index, columns=["elo_home", "elo_away"])


def current_elo(matches: pd.DataFrame, **kw) -> dict:
    """Elo etter siste spilte kamp, per (liga, lag)."""
    e = elo_ratings(matches, **kw)
    last = {}
    k, hfa = kw.get("k", 20.0), kw.get("hfa", 60.0)
    for (lg, h, a, hg, ag), (rh, ra) in zip(matches[["league", "home", "away", "hg", "ag"]].itertuples(index=False), e.to_numpy()):
        ex = 1 / (1 + 10 ** (-(rh + hfa - ra) / 400))
        s = 1.0 if hg > ag else 0.5 if hg == ag else 0.0
        d = k * (np.log(abs(hg - ag) + 1) + 1) * (s - ex)
        last[(lg, h)] = rh + d
        last[(lg, a)] = ra - d
    return last
