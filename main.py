"""
CLI entrypoint for the regulatory-updates ingester.

Usage:
  python main.py                 # fetch all sources once and upsert to MySQL
  python main.py --probe         # fetch only; print per-source item counts +
                                 # a sample title. Use this to tune the scrape
                                 # selectors in config.py at deploy time. No DB.
  python main.py --schedule      # run continuously, re-polling every
                                 # SH_POLL_INTERVAL_MINUTES (default 60)

Deploy: run `--schedule` under systemd / a container, OR drop the plain
one-shot form into system cron (e.g. hourly). Either way the Yii backend only
reads the table this writes.
"""

import argparse
import logging
import sys

import config
import ingest


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def cmd_probe():
    """Fetch every source and report health without touching the DB."""
    print("Probing %d source(s)...\n" % len(config.SOURCES))
    grand_total = 0
    problems = []
    for s in config.SOURCES:
        rows = ingest.fetch_one(s)
        grand_total += len(rows)
        sample = rows[0]["title"][:80] if rows else "(no items)"
        flag = "  OK " if rows else " WARN"
        print("[%s] %-18s %3d  e.g. %s" % (flag, s["key"], len(rows), sample))
        if not rows:
            problems.append(s["key"])
    print("\nTotal items: %d" % grand_total)
    if problems:
        print("Needs attention (0 items -> check URL/selectors in config.py): %s"
              % ", ".join(problems))
        return 1
    return 0


def cmd_run_once():
    n = ingest.run_once()
    print("Done. Upserted %d row(s)." % n)
    return 0


def cmd_schedule():
    from apscheduler.schedulers.blocking import BlockingScheduler

    log = logging.getLogger("rss_ingest")
    interval = config.POLL_INTERVAL_MINUTES
    sched = BlockingScheduler()

    def job():
        try:
            ingest.run_once()
        except Exception as e:
            log.error("scheduled run failed: %s", e)

    job()  # run immediately on start
    sched.add_job(job, "interval", minutes=interval, id="regulatory_poll",
                  max_instances=1, coalesce=True)
    log.info("scheduler started; polling every %d minute(s)", interval)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


def main(argv=None):
    _setup_logging()
    ap = argparse.ArgumentParser(description="SafeHands regulatory-updates ingester")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true", help="fetch only, print counts, no DB")
    g.add_argument("--schedule", action="store_true", help="run continuously on a timer")
    args = ap.parse_args(argv)

    if args.probe:
        return cmd_probe()
    if args.schedule:
        return cmd_schedule()
    return cmd_run_once()


if __name__ == "__main__":
    sys.exit(main() or 0)
