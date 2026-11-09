# Kjøring før avspark (planlagt oppgave kl. 12:41 og 17:41)

Planlagt oppgave «Oddsboka før avspark (sky)», kl. 12:41, 14:41, 17:41 og 19:41.

Prompten ligger i selve den planlagte oppgaven og endres der (den er også utvidet med landslagssporet fra en annen økt). Den gjeldende teksten hentes med list_triggers. Denne fila holdes derfor ikke lenger som en ordrett kopi, så den ikke kan bli utdatert og misvisende.

Viktige regler per 9. nov 2026:
- Ikke Firecrawl. Gratis kilder: NT i headless nettleser, Pinnacle i nettleser eller OddsPortal, resultater fra FotMob/Sofascore, ellers ESPN, BetExplorer eller Flashscore, bekreftet av to kilder.
- Sluttpris for CLV: Pinnacle/Betfair etter kampen, ellers siste måling fra kjøringen før avspark (maks 4 timer før), ellers markedssnitt. closing_source sier hvilken.
- closing_nt_odds = siste NT-måling før avspark (nt_odds_late).
- Tidene i predictions.json er norsk tid, og fila har alltid bet_rule, staking og staking_spec, også uten kamper.
- Live-valg: følg model/live.selection når status er «aktiv».
