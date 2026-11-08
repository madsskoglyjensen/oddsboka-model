# Nattoppgjøret (planlagt oppgave kl. 00:00)

```text
Du gjør opp ferdigspilte fotballbets i SJs paper trading-prosjekt «Oddsboka» (metode v2.1). Dette er en ren oppgjørskjøring om natten: du analyserer ikke kamper, finner ikke nye bets og låser ingenting. Et feil oppgjør er verre enn et som venter til morgenkjøringen kl. 08:00.

KONTEKST
- Ren paper trading med fiktiv bankroll (start 100 USD per spor). Ingen ekte penger.
- All tilstand ligger i databasen til artifactet https://claude.ai/artifact/B5QpUXS568brJm3bcUh715 . Les og skriv med ArtifactData (last med ToolSearch "select:ArtifactData"). Bruk batch, og pass if_version fra siste lesing på dokumenter som finnes.

OPPGAVE
1. Les bets og coupons med status "open". Ta bare bets der avspark + 2,5 timer er før nå.
2. Hent sluttresultat fra FotMob eller Sofascore. Kampen må være markert ferdigspilt. Sett status "won"/"lost"/"void" (utsatt/avlyst = void) og result (f.eks. "2-1"). Uklart: la stå åpen og logg det.
3. Sluttodds fra en offentlig kilde (OddsPortal, Flashscore e.l.):
   - closing_sharp_odds = Pinnacle-sluttodds for alle tre HUB-utfall (ellers Betfair Exchange), og closing_source.
   - closing_nt_odds = NT-oddsen rett før avspark for utfallet vi spilte, hvis den finnes.
   - clv = odds × p_sharp_close − 1, der p_sharp_close er skarp sluttpris uten margin (1/odds delt på summen av 1/odds) for utfallet vi spilte.
   - Finnes ikke skarp sluttodds: bruk markedssnitt, skriv det i closing_source. Finnes ingen sluttodds: clv null og årsak i notes. Ikke gjett.
4. pnl per spor: vunnet = innsats × (odds − 1), tapt = −innsats, void = 0. Skriv pnl_main, pnl_flat, pnl_all og settled_at (ISO med +01:00/+02:00).
5. Kuponger (kind "kombo" og "hoyrisiko"): vunnet når alle ledd er vunnet (void-ledd teller odds 1,00, totalodds regnes på nytt i kode), tapt så snart ett ledd er tapt, ellers åpen. pnl og settled_at.
6. Dagsnapshot til bankroll_history/<dagens dato>: main = 100 + sum pnl_main + sum pnl for kombo-kuponger; flat = 100 + sum pnl_flat; all = 100 + sum pnl_all; coupon = 100 + sum pnl for høyrisiko-kuponger; open_exposure = åpne stake_main + innsats på åpne kombo-kuponger. Finnes dokumentet, oppdater med if_version.
7. Skriv run_log/<dagens dato>-natt med date ("<dagens dato> natt"), started, finished, errors (liste) og data_gaps (liste), og hvilke kamper som ble gjort opp og hvilke som står åpne og hvorfor.

AVGRENSNINGER
- Endre aldri låste felt (odds, nt_odds, own_p, p_sharp, edge, innsats, locked_at, reasoning osv.). Bare oppgjørsfeltene over og notes.
- Opprett aldri nye bets eller kuponger, endre aldri config, og publiser aldri artifact-siden.
- Ingen e-post, varsler eller meldinger. Dashboardet er eneste output.

KVALITET
Før du avslutter: sjekk at hvert resultat stemmer med utfallet vi spilte, regn kupongenes totalodds og pnl på nytt i kode, og avstem at hvert spors bankroll = 100 + sum pnl. Feil rettes og noteres i run_log.

Ingen å spørre. Er noe usikkert, la betten stå åpen til morgenkjøringen og logg årsaken.
```
