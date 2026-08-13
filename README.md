# Regulatory Updates Ingester

Standalone Python service that polls the Indian financial regulators' feeds and
writes them into the SafeHands MySQL table `regulatory_updates`. The Yii backend
only **reads** that table (`/v2/RegulatoryUpdates/Latest`, `/List`); this service
is the sole writer. Powers the admin-dashboard "Regulatory Updates" widget and
the Compliance screen at `/regulatory-updates`.

## Sources
| Regulator | How | Status |
|-----------|-----|--------|
| SEBI (press/circulars/orders) | RSS `sebirss.xml` | confirmed |
| RBI Press Releases | RSS `pressreleases_rss.xml` | confirmed |
| RBI Notifications | RSS `notifications_rss.xml` | confirmed |
| BSE Notices | RSS | **confirm URL** |
| NSE Announcements | RSS | **confirm URL** (NSE is bot-protected) |
| AMFI Circulars (news, **not** NAV) | scrape | selectors set (validated 2026-08-13); date parsed from filename |
| IRDAI Circulars | scrape | selectors set (validated 2026-08-13) |
| PFRDA Circulars | scrape | selectors set (validated 2026-08-13); list has no per-item date |

All sources are edited in one place: `config.py` -> `SOURCES`. Adding a regulator
later = one entry here (+ optionally a label on the React screen's filter). No DB
or API change.

## Setup
```bash
cd python_RSS_ingest
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then fill in DB creds
```
Create the table once (from the repo root):
```bash
mysql -u <user> -p <db> < ../safehands_backend/new_db_script/regulatory_updates.sql
```

## Run
```bash
python main.py --probe     # fetch only; prints per-source counts + a sample.
                           # Use this to tune scrape selectors until all sources
                           # report items. Touches no DB.
python main.py             # fetch all + upsert once (safe to re-run; deduped)
python main.py --schedule  # run continuously, re-poll every SH_POLL_INTERVAL_MINUTES
```

## Deploy
Either run `main.py --schedule` under systemd / a container, or add the one-shot
form to cron:
```
0 * * * *  cd /opt/safehands/rss_ingest && .venv/bin/python main.py >> /var/log/reg_ingest.log 2>&1
```

## Tuning the scrape sources (AMFI / IRDAI / PFRDA)
These regulators publish no RSS, so we scrape their circular pages. Open the page,
find the row/link markup, and set `row_selector` / `link_selector` (and optionally
`title_selector`, `date_selector`) in `config.py`. Re-run `--probe` until the count
looks right. Be a good citizen: respect each site's robots.txt/ToS and the polling
interval.

## Notes
- Dedupe key is `sha256(source|url|title)` (unique index `uq_ru_hash`); re-runs
  update existing rows in place rather than duplicating.
- This is public regulator data and is not tenant-scoped (no `company_master_id`).
- Data is informational, not a system of record; the UI always links back to the
  official source and keeps `raw_json` for audit.
```
