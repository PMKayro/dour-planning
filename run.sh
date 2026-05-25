#!/bin/sh
# Lance par le cron sur T-600 : maj du code + scrape + sync Airtable.
# Les secrets (token, base id) sont dans ~/dour-planning/.env (non commite).
cd /root/dour-planning || exit 1
git pull -q
set -a
. /root/dour-planning/.env
set +a
/root/dour-planning/.venv/bin/python main.py
