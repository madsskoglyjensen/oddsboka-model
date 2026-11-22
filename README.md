# Oddsboka-modellen

Statistisk modell bak [Oddsboka](https://claude.ai/artifact/B5QpUXS568brJm3bcUh715): paper trading på fotball (topp 5-ligaene + Championship, League One, 2. Bundesliga og Serie B), fiktiv bankroll på $100. Ingen ekte penger.

## Slik henger det sammen

| Steg | Når | Hva |
|---|---|---|
| `src/data.py` | daglig | Henter resultater og odds fra football-data.co.uk (2015/16 →) |
| `src/predict.py` | daglig | Refit lagstyrker til i dag, skriver `out/predictions.json` med sannsynligheter og `nt_min_odds` |
| `src/validate.py` | daglig | Datasjekker: fjerner dubletter og umulige odds før noe trenes |
| `src/tune.py` | søndag | Optuna-søk på modellparametere (tidsvekt, krymping, xG-vekt), endrer bare ved reell forbedring |
| `src/base.py` | søndag | Walk-forward: grunnmodellene for hver kamp, trent bare på data før kampuka |
| `src/learn.py` | søndag | Stacking-modeller, evaluering sesong for sesong, betting-simulering, innsatseksperimenter, ligafilter, valg av mester og spillregel |
| `src/live_learn.py` | søndag (Claude-forsker) | Lærer av egne live-kamper: når NT er treg, om modellen spår sluttodds, og live-kappløpet |
| `src/dashboard.py` | søndag | Skriver `out/dashboard.json` som morgenkjøringen legger inn i dashboardet |

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
3. Innsatsregel = den av flat, trapp, proporsjonal, ¼ Kelly og ½ Kelly som vokser raskest med maks drawdown under 25 %.
4. Ligafilter = ligaer der regelen har positiv CLV (eller for få bets til å dømme).

## Viktige forbehold

- NT-odds finnes ikke historisk. Bet365 brukes som proxy, med 1 % trekk (`nt_odds_factor` 0,99).
- NT-kalibrering (`out/nt_calibration.json`, skrives av `live_learn.py` fra kamper logget live): når en liga har minst 40
  målinger av NT mot Pinnacle, bruker backtesten ligaens ekte nivå: faktor = (NT/Pinnacle live) / (Bet365/Pinnacle historisk).
  Bet365/Pinnacle regnes bare på rader der den skarpe prisen faktisk er Pinnacle (`sharp_src == "PS"` i `data/matches.csv`),
  fra nyeste sesong med minst 100 slike kamper. Betfair Exchange (skarp pris i 2026/27) har et annet nivå og blandes ikke inn.
- NT etter oddsnivå: live er NT 1–2 % under Pinnacle på korte odds og 5–6 % på lange, mer enn Bet365. Koden kan bruke en faktor
  per oddsbånd (`NT_BANDS` i learn.py), men den er av (`"nt_bands": false` i config.json) til SJ har sett effekten.
  `nt_bands_shadow` i metrics.json og et funn i dashboardet viser hver søndag hva spillregelen ville gitt med båndene på.
- Skarp pris i `fixtures.csv` er et øyeblikksbilde (oppdateres tirsdag og fredag), så live kan prisen ha flyttet seg.

## Ærlig testing

Hver søndag velges spillregel og innsats også bare på eldre sesonger, og testes på de tre siste sesongene som valget aldri har sett (`holdout` i `out/metrics.json`). Forskjellen mellom «trent» og «urørt» viser hvor mye backtesten overdriver. Hver læringsrunde logges i `out/experiments.json`.

## ML-lab (src/viz.py)

Kjøres hver morgen etter `predict.py`. Henter innmaten fra de ekte trente modellene og flettes inn i `out/dashboard.json` (feltet `viz`):
vektene i logistisk regresjon, de øverste nivåene i et tre fra random forest og hvordan alle trærne stemmer per kamp,
hvordan LightGBM bygger sannsynligheten tre for tre, alle vektene i det nevrale nettet (dashboardet regner fremoverpasset selv),
Elo-kurver for topplagene og feature-viktighet (permutasjon per feature-gruppe, beregnes på søndager).

## Daglig kjøring uten GitHub-avhengighet

GitHub sine planlagte jobber er upålitelige (forsinket i timevis eller hoppet over). Morgenkjøringen kl. 08:00
bygger derfor modellfila selv med `bash scripts/build_daily.sh` (3–5 min) og legger den i databasen som
`model/predictions`, som kjøringene før avspark leser. GitHub-jobben prøver hvert 20. minutt fra ca. 01:00 til 11:40
og er reserve for dagsfila og hovedvei for den ukentlige læringen (søndag, eller første dag etter hvis søndagen ble hoppet over).

## Reservekilde for kamplista

football-data.co.uk oppdaterer `fixtures.csv` bare et par ganger i uka. Kamper morgenkjøringen finner selv (NT, FotMob,
Pinnacle), men som mangler i `predictions.json`, skrives til `data/extra_fixtures.json` med skarp pris, og
`bash scripts/add_extra.sh` gir dem sannsynligheter fra modellen. Lagnavn matches automatisk mot navnene i football-data
(`src/fixtures_extra.py`); det som ikke matcher, står i `extra_issues` i predictions.json.

## NT-treghet med gradient boosting (live_learn.py)

Når 150 kamper er logget, konkurrerer logistisk regresjon, beslutningstre og LightGBM om å spå når NT henger etter
(klassifisering, AUC) og hvor god NT-prisen blir (regresjon på NT-odds × skarp sannsynlighet − 1, R², kamper holdt samlet i
kryssvalideringen). Beste modell vises i dashboardet.
