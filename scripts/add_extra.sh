#!/usr/bin/env bash
# Legger til kamper morgenkjøringen fant selv (data/extra_fixtures.json) når football-data ikke har dem i fixtures.csv.
# Kjøres etter build_daily.sh. Tar under ett minutt.
#   bash scripts/add_extra.sh [data/extra_fixtures.json]
set -euo pipefail
cd "$(dirname "$0")/.."
F="${1:-data/extra_fixtures.json}"
python3 src/predict.py --no-download --extra "$F"
python3 src/viz.py >/dev/null
python3 - <<'PY'
import json
p = json.load(open("out/predictions.json"))
n = sum(1 for r in p["fixtures"] if r.get("fixture_source") == "morgenkjøringen")
print(f"OK: {len(p['fixtures'])} kamper i predictions.json, {n} fra reservelista. Problemer: {json.dumps(p.get('extra_issues', []), ensure_ascii=False)}")
PY
