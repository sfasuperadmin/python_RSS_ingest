# Regulatory Updates feed — file handoff

All files for the "Regulatory Updates" feature (SEBI / RBI / AMFI / BSE / NSE /
IRDAI / PFRDA news on the Admin dashboard + a Compliance screen). Paths below
mirror where each file lives in the repos, so you can drop them straight in.

## NEW files (add as-is)
```
rss_ingest/                                  ← standalone Python ingester (new top-level folder)
  config.py                                  source registry (feeds + selectors) + env config
  ingest.py                                  fetch (RSS + scrape), normalize, dedupe, MySQL upsert
  main.py                                    CLI: --probe / one-shot / --schedule
  requirements.txt
  .env.example                               copy to .env, fill DB creds
  README.md                                  run/deploy/tuning notes

safehands_backend/
  new_db_script/regulatory_updates.sql       cache table (run once)
  backend/controllers/v2/RegulatoryUpdatesController.php   read-only API: Latest + List

safehands_react/
  src/screens/Admin/Compliance/RegulatoryUpdates/RegulatoryUpdates.tsx      Compliance screen
  src/screens/DataEntryOperator/Dashboard/RegulatoryUpdatesCard.tsx         dashboard widget
```

## MODIFIED files (full working copies included — review the marked edits)
```
safehands_react/src/navigation/index.tsx
    • added a lazy import next to the other Compliance imports:
        const RegulatoryUpdates = React.lazy(() => import("../screens/Admin/Compliance/RegulatoryUpdates/RegulatoryUpdates"));
    • added a <Route path="/regulatory-updates"> just after the /compliance-header route

safehands_react/src/screens/DataEntryOperator/Dashboard/AdminDashBoard.tsx
    • imported RegulatoryUpdatesCard
    • added `const REGULATORY_UPDATES_MENU_ID = 700;` (change to the real menu_id you assign)
    • added the menu-gated widget block after the AdminDashboardTask block
```
> These two are large existing files. If you prefer, apply just the marked edits
> to your current copies instead of overwriting (in case the files moved on).

## Deploy checklist
1. **DB:** run `safehands_backend/new_db_script/regulatory_updates.sql` on the app DB.
2. **Ingester:** `cd rss_ingest`, create a venv, `pip install -r requirements.txt`,
   `cp .env.example .env` and fill DB creds.
3. **Confirm feeds:** `python main.py --probe`. Scrape selectors for AMFI/IRDAI/PFRDA
   are set + validated. Still to confirm: the **BSE** and **NSE** RSS URLs
   (marked `NEEDS-CONFIRM` in config.py; NSE is bot-protected — may need a
   browser-like User-Agent / primed cookie).
   - If you're behind a corporate proxy and see TLS errors, set `SH_CA_BUNDLE`
     to the proxy CA in `.env` (don't disable verification).
4. **Run it:** `python main.py` once to backfill, then deploy `python main.py --schedule`
   (systemd/container) or add the one-shot form to cron (hourly).
5. **Menus (via the app's Menu Organizer, not raw SQL):**
   - Add a Compliance child menu item → react_url `/regulatory-updates`
     (so it shows in the sidebar and as a card on /compliance-header).
   - Add the dashboard-widget menu item, note its menu_id, and set
     `REGULATORY_UPDATES_MENU_ID` in AdminDashBoard.tsx to that id.
6. **Verify:** load `/dashboard` (widget) and `/regulatory-updates` (screen).

## Notes
- Data is public regulator news (no client PII, not tenant-scoped).
- The ingester is the only writer; the Yii controller only reads.
- Adding a future source = one entry in config.py `SOURCES` + one label on the
  screen's regulator filter. No schema/API change.
```
