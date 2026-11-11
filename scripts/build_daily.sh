#!/usr/bin/env bash
# Lager dagens modellfil lokalt, uten å være avhengig av at GitHub kjører planlagte jobber.
# Brukes av morgenkjøringen (planlagt oppgave kl. 08:00). Tar ca. 3–4 minutter i skyen.
#   bash scripts/build_daily.sh
# Resultat: out/predictions.json, out/model_viz.json og out/dashboard.json (med fersk viz).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m pip install -q -r requirements.txt 2>/dev/null || python3 -m pip install -q --break-system-packages -r requirements.txt
python3 src/data.py          # ferske resultater og kampliste fra football-data.co.uk
python3 src/predict.py --no-download
python3 src/viz.py
python3 - <<'PY'
import json
p = json.load(open("out/predictions.json"))
print(f"OK: predictions.json generert {p['generated']}, {len(p['fixtures'])} kamper, regel {p['bet_rule'].get('model')} {p['bet_rule'].get('edge_min')}, innsats {p['staking']}")
PY
