#!/bin/bash
# One full engine run. Point cron/launchd at this.
#   chmod +x run-daily.sh
#   ./run-daily.sh
# caffeinate stops a laptop sleeping mid-run (a sleep kills the scrape).
cd "$(dirname "$0")" || exit 1
mkdir -p logs
exec caffeinate -i python3 engine/main.py >> "logs/run-$(date +%F).log" 2>&1
