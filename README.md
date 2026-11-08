# Oddsboka-modellen

Statistisk modell bak [Oddsboka](https://claude.ai/artifact/B5QpUXS568brJm3bcUh715): paper trading på fotball (topp 5-ligaene), fiktiv bankroll på $100. Ingen ekte penger.

## Slik henger det sammen

| Steg | Når | Hva |
|---|---|---|
| `src/data.py` | daglig | Henter resultater og odds fra football-data.co.uk (2015/16 →) |
| `src/predict.py` | daglig | Refit lagstyrker til i dag, skriver `out/predictions.json` med sannsynligheter og `nt_min_odds` |
| `src/tune.py` | søndag | Koordinatsøk på modellparametere (tidsvekt, krymping, xG-vekt), endrer bare ved reell forbedring |
| `src/base.py` | søndag | Walk-forward: grunnmodellene for hver kamp, trent bare på data før kampuka |
| `src/learn.py` | søndag | Stacking-modeller, evaluering sesong for sesong, betting-simulering, valg av mester og spillregel |

Morgenkjøringen i Claude leser `out/predictions.json`, henter NT-odds og låser bets i dashboardet.

## Modellene

- **Marked**: odds uten bookmakermargin (snitt og skarp: Pinnacle, ellers Betfair Exchange).
- **Dixon-Coles**: angrep/forsvar per lag med eksponentiell tidsvekt, på mål og på en skuddbasert xG-proxy.
- **Elo** med målmargin.
- **Stacking**: logistisk regresjon og LightGBM som kombinerer alt over.

## Hvordan den lærer

Hver søndag trenes alt på nytt med alle nye kamper, parametere tunes, og to valg tas på data:
1. Sannsynlighetsmester = lavest log loss siste tre sesonger.
2. Spillregel = modell + edge-terskel med størst samlet CLV mot skarp sluttodds (krav: CLV > 0, minst 150 bets).

## Viktige forbehold

- NT-odds finnes ikke historisk. Bet365 brukes som proxy (lå likt med NT mot skarp pris i november 2026), med 1 % trekk.
- Skarp pris i `fixtures.csv` er et øyeblikksbilde (oppdateres tirsdag og fredag), så live kan prisen ha flyttet seg.
