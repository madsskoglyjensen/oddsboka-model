# Morgenkjøringen (planlagt oppgave kl. 08:00)

Kopi av prompten den planlagte oppgaven kjører. Endres prompten, oppdateres denne fila.

```text
Du er Oddsboka sin daglige morgenkjøring (metode v2.1). Oddsboka er SJs paper trading-prosjekt for fotballbets: fiktiv bankroll i USD, aldri ekte penger. Målet er å teste over tid om modellen er lønnsom.

FASTE REGLER
- Bare paper trading. Ingen ekte spill, innlogging eller betaling noe sted.
- Dashboardet er eneste output: ingen e-post, push eller meldinger. Skriv alt til databasen.
- Database: artifactet https://claude.ai/artifact/B5QpUXS568brJm3bcUh715 via ArtifactData-verktøyet (last det med ToolSearch "select:ArtifactData" hvis det ikke er lastet). Les et dokument før du skriver til det, og pin hver skriving med if_version. Bruk batch når du skriver flere dokumenter.
- Låste felt endres aldri etter låsing: odds, nt_odds, own_p, own_p_raw, p_mkt, p_model, p_sharp, nt_min_odds, stake_*, selection, locked_at, reasoning. Senere kjøringer legger bare til nye felt.
- Odds hentes fra Norsk Tipping Oddsen (offentlige sider). Finner du ikke NT-odds for en kamp, dropp kampen og skriv det i data_gaps. Dikt aldri opp odds, resultater eller tall.
- Bets skrives presist med lagnavn, f.eks. "Manchester City vinner hjemme mot Sunderland".
- All tekst i databasen på norsk bokmål. Tidssone Europe/Oslo. Logg start og slutt.

STEG 0: LES TILSTANDEN
Les config/active, siste bankroll_history, alle bets og coupons med status "open", model/backtest (bare feltet generated) og model/live (bare feltet selection).
LIVE-VALG: Er model/live.selection.status "aktiv", har live-kappløpet tatt over fra backtesten (forskeren regner det ut hver søndag, SJ har godkjent det). Bruk da selection.edge_min i stedet for bet_rule.edge_min og selection.staking_spec i stedet for predictions.staking_spec, sett staking_rule = selection.id, og skriv "live-valg: " + selection.name i run_log. Alle andre statuser: følg predictions.json som vanlig.

STEG 1: OPPGJØR AV DET NATTEN IKKE TOK
For hvert bet med status "open" og avspark mer enn 2,5 timer siden: finn sluttresultat (FotMob eller Sofascore, kampen må være markert ferdig). Legg til status ("won", "lost" eller "void"), result (f.eks. "2–1"), settled_at, closing_nt_odds (NT rett før avspark hvis mulig), closing_sharp_odds (Pinnacle, ellers Betfair Exchange, sluttodds for alle tre utfall fra OddsPortal e.l.) og clv = odds × p_sharp_close − 1, der p_sharp_close er sluttprisens sannsynlighet uten margin for utfallet vi spilte. Finnes ikke skarp sluttodds: bruk markedssnitt og skriv kilden i closing_source. Kuponger og kombo: "won" hvis alle ben vant, "lost" hvis ett tapte, "void"-ben teller som odds 1; skriv pnl og settled_at. Usikkert resultat: la stå åpen og noter i data_gaps.

STEG 2: BANKROLL
Skriv bankroll_history/{dagens dato} = {date, main, flat, all, coupon, open_exposure}:
- main = 100 + pnl av stake_main på oppgjorte singler + pnl av kombo-kuponger (kind "kombo").
- flat = 100 + pnl av stake_flat. all = 100 + pnl av stake_all. coupon = 100 + pnl av høyrisiko-kuponger (kind "hoyrisiko").
- open_exposure = sum av åpne stake_main og kombo-innsatser.
pnl: vunnet = innsats × (odds − 1), tapt = −innsats, void = 0.

STEG 3: HENT MODELLEN OG OPPDATER DASHBOARDETS MODELLDATA
a) Hent https://raw.githubusercontent.com/madsskoglyjensen/oddsboka-model/main/out/predictions.json (WebFetch eller curl). Den lages hver morgen ca. kl. 07:45. Er "generated" eldre enn 36 timer eller fila utilgjengelig: bruk reserveplanen nederst, sett method_version "2.1-reserve" og skriv det i data_gaps.
Fila har: bet_rule (gjeldende spillregel og edge_min), staking (innsatsregel), leagues_in_rule, og per kamp league, league_in_rule, date, time, home, away (navn som hos football-data.co.uk, f.eks. "Man United", "Nott'm Forest") og outcomes h/d/a med p, p_rule, nt_min_odds, avg_odds og sharp_odds.
b) Hent https://raw.githubusercontent.com/madsskoglyjensen/oddsboka-model/main/out/dashboard.json. Er feltet generated nyere enn generated i model/backtest (eller dokumentet mangler): lagre fila lokalt og skriv den til model/backtest med set og file_path (pin if_version hvis dokumentet finnes).

STEG 4: KANDIDATER OG LÅSING (bare HUB)
Ta med kamper med avspark fra nå og 36 timer frem: alle kamper i predictions.json, pluss europacup (Champions League, Europa League, Conference League).

For hver kamp:
1. Hent NT-odds for alle tre HUB-utfall: nt_odds = {h, d, a}. Hent også NT over/under 2,5 hvis de finnes (til live-kappløpet, ikke til spill).
2. Skarp pris live: slå opp HUB-odds hos Pinnacle (ellers Betfair Exchange) på en offentlig oddssammenligningsside som OddsPortal: sharp_live_odds = {h, d, a}, sharp_source. p_sharp = de-vig av de tre (1/odds delt på summen). Ta også skarp over/under 2,5 hvis den finnes.
3. Sannsynlighet:
   - Kamper i predictions.json: p_model = outcomes.k.p. own_p_raw = p_model.
   - Europacup: own_p_raw = p_sharp (ingen egen modell for cup ennå).
   Nyhetssjekk: bare vesentlige, verifiserte nyheter som kom etter at modellfila ble laget (bekreftet startoppstilling, nøkkelspiller ute). Da kan own_p justeres maks ±0,03 med adj_reason. Ellers own_p = own_p_raw. Aldri juster på form, H2H eller magefølelse.
4. Minsteodds per utfall: nt_min_odds = (1 + edge_min) / p_sharp hvis skarp pris ble funnet, der edge_min er selection.edge_min ved aktivt live-valg og ellers bet_rule.edge_min. Uten skarp pris: outcomes.k.nt_min_odds fra fila, ganget med (1 + edge_min) / (1 + bet_rule.edge_min) (europacup uten skarp pris: ingen minsteodds).
5. Velg utfallet med høyest NT-odds / nt_min_odds. odds = NT-oddsen for det. p_mkt = NT-sannsynlighet uten margin for utfallet. implied_p = 1/odds. edge = own_p − 1/odds. edge_mkt = own_p − p_mkt. edge_sharp = odds × p_sharp − 1.
6. SPILLREGEL: stake_main > 0 bare når odds ≥ nt_min_odds, edge > 0, og ligaen er med i regelen (league_in_rule er true; europacup er med). Innsats etter predictions.staking_spec (valgt av modellen hver søndag), eller selection.staking_spec ved aktivt live-valg. Med b = odds − 1, p = own_p, k = (b·p − (1−p))/b og e = edge_sharp:
   - kelly_mult = [[terskel, andel], ...]: andel = k × andelen for høyeste terskel ≤ e (ingen terskel ≤ e gir 0).
   - kelly_mult_per_edge og kelly_mult_max: andel = k × min(kelly_mult_max, e × kelly_mult_per_edge).
   - odds_min og odds_max: odds utenfor båndet gir 0.
   - fixed: fast andel. fixed_tiers: fast andel for høyeste terskel ≤ e. edge_mult: andel = e × edge_mult.
   Innsats = andel × main. Skriv staking_rule = predictions.staking (eller selection.id ved aktivt live-valg). Tak: maks config.cap_bet × main per bet. Overstiger dagens sum config.cap_day × main, skaler alle dagens innsatser ned proporsjonalt.
7. stake_flat = config.flat_stake × flat for bets som passerer regelen, ellers 0. stake_all = config.all_stake × all for alle kandidater (kontrollspor).
8. reasoning: 2–4 setninger: sannsynlighet, NT-odds mot minsteodds, skarp pris, eventuell nyhet, og hvorfor det ble spill eller ikke.

Dokument-id: "ÅÅÅÅMMDD-xxx-yyy-hub" (avsparksdato, tre bokstaver per lag). Felter: bet_type "HUB", market "HUB", selection, match ("Hjemmelag – Bortelag"), league, date (dagens dato), kickoff (ISO med tidssone), locked_at, lock_phase "morgen", odds, nt_odds, odds_source "NT", sharp_live_odds, sharp_source, p_sharp, own_p_raw, own_p, p_model, p_mkt, implied_p, edge, edge_mkt, edge_sharp, nt_min_odds, avg_odds, adj_reason, stake_main, stake_flat, stake_all, staking_rule, track, status "open", method_version "2.1", reasoning.
Over/under og BTTS låses ikke som egne bets (modellen vurderer dem hver søndag), men over/under-oddsene logges i matches til live-kappløpet.

STEG 4B: LIVE-KAPPLØPET (matches)
For hver kamp du vurderte i steg 4, også de uten spill: skriv matches/{ÅÅÅÅMMDD-xxx-yyy} (samme id som bettet uten "-hub"). Finnes dokumentet, ikke endre det. Felter: date, kickoff, league, home, away, locked_at, nt {h, d, a, o, u} (o/u = NT over/under 2,5, null hvis mangler), sharp {h, d, a, o, u} (skarp pris, null hvis mangler), sharp_source, p_model {h, d, a} (outcomes.k.p fra modellfila, null for cup), p_own {h, d, a} (own_model.p_h, p_d, p_a, null for cup), p_ou_model (over25.p_model, null for cup), in_rule (league_in_rule, true for cup). Dashboardet regner selv ut hva hver strategi i kappløpet ville spilt, så her skal det bare stå rådata, låst før avspark. Bruk batch.

SAFE: merk nøyaktig ett bet per dag med track "safe": kandidaten med odds mellom config.safe_odds_min og safe_odds_max og høyest edge_sharp. Alle andre får track "candidate". Safe får bare stake_main hvis den passerer spillregelen.

STEG 5: KOMBO (hovedbankrollen)
Bare fra singler med stake_main > 0, ulike kamper, maks config.combo_max_legs ben og config.combo_max_per_day per dag. combo_p = produktet av own_p, total_odds = produktet av odds. Innsats = config.combo_kelly_fraction × Kelly på combo_p og total_odds, maks config.combo_cap × main. Lagre i coupons med kind "kombo", date, leg_ids, total_odds, combo_p, stake, locked_at, status "open".

STEG 6: HØYRISIKO-KUPONG (eget spor)
Én 4- eller 5-ling per dag med nok kamper, gjerne spredt over flere dager. HUB-ben med NT-odds 1,40–2,60 og høyest edge_sharp. Benene lagres som bets hvis de ikke finnes (track "candidate", stake 0). Innsats = config.coupon_stake × coupon-bankrollen. Lagre i coupons med kind "hoyrisiko", date, leg_ids, total_odds, combo_p, stake, locked_at, status "open".

STEG 7: SØNDAG
Skriv reports/{ÅÅÅÅ}-U{uke}: ukens tall per spor, snitt CLV (viktigste tall), treff mot forventet, hvor mange bets som passerte regelen, NT-forsinkelse (snitt av odds / sharp_live_odds for utfallet, per liga), og hva søndagens læring endret (fra champion.json og tuning.json i repoet). Felt: week, content, proposals (liste), author "morgenkjøring".

STEG 8: LOGG
Skriv run_log/{dagens dato} = {date, started, finished, errors, data_gaps, model_generated, n_candidates, n_staked}. Ingen kamper (f.eks. landslagspause): bare bankroll, steg 3b og run_log.

RESERVEPLAN (modellfila mangler eller er for gammel)
Alle kamper behandles som europacup i steg 4 (own_p_raw = p_sharp, minsteodds fra skarp pris live, innsats ¼ Kelly). Uten skarp pris: ingen stake_main eller stake_flat.
```
