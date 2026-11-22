# Morgenkjøringen (planlagt oppgave kl. 08:00)

Planlagt oppgave «Oddsboka morgen v2.1 (sky)», kl. 08:00.

Prompten ligger i selve den planlagte oppgaven og endres der (den er også utvidet med landslagssporet fra en annen økt). Den gjeldende teksten hentes med list_triggers. Denne fila holdes derfor ikke lenger som en ordrett kopi, så den ikke kan bli utdatert og misvisende.

Viktige regler per 22. nov 2026:
- Ikke Firecrawl. Gratis kilder: NT i headless nettleser, Pinnacle i nettleser eller OddsPortal, resultater fra FotMob/Sofascore, ellers ESPN, BetExplorer eller Flashscore, bekreftet av to kilder.
- Sluttpris for CLV: Pinnacle/Betfair etter kampen, ellers siste måling fra kjøringen før avspark (maks 4 timer før), ellers markedssnitt. closing_source sier hvilken.
- closing_nt_odds = siste NT-måling før avspark (nt_odds_late).
- Tidene i predictions.json er norsk tid, og fila har alltid bet_rule, staking og staking_spec, også uten kamper.
- Live-valg: følg model/live.selection når status er «aktiv».
- Reservekamper: ligakamper som mangler i predictions.json (football-data oppdaterer kamplista sjelden) skrives til data/extra_fixtures.json med skarp pris, og `bash scripts/add_extra.sh` gir dem sannsynligheter fra modellen. Lagnavn som ikke finnes, står i extra_issues og behandles etter reserveplanen.
