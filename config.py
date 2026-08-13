"""
Source registry + runtime config for the SafeHands regulatory-updates ingester.

Each entry in SOURCES is one feed the service polls. Two kinds:

  type="rss"     -> parsed with feedparser; only `url` is required.
  type="scrape"  -> parsed with BeautifulSoup using the CSS selectors below,
                    for regulators that publish no RSS (AMFI news, IRDAI, PFRDA).

CONFIRMED urls were validated against the live site while building this.
NEEDS-CONFIRM urls/selectors are best-known values that MUST be verified on the
live page at deploy time -- run `python main.py --probe` and tune until every
source reports a healthy item count. Nothing else in the stack changes when you
edit this file: add/adjust an entry here and (optionally) a label on the React
screen's filter, and you are done.

DB + runtime settings come from environment variables (see .env.example).
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv is optional; env vars may be set by the OS/container
    pass


# ---------------------------------------------------------------------------
# Feed / page registry
# ---------------------------------------------------------------------------
# Common fields:
#   key       unique id for this feed (logging only)
#   source    value stored in regulatory_updates.source (drives the UI filter)
#   category  feed sub-label stored in regulatory_updates.category
#   type      "rss" | "scrape"
#   url       feed or page URL
# Scrape-only fields (CSS selectors, relative to the page):
#   row_selector    selects each item row/card (required for scrape)
#   link_selector   selects the <a> inside a row   (default: "a")
#   title_selector  selects the title text node     (default: the link text)
#   date_selector   selects a date text node        (default: none)

SOURCES = [
    # ---- SEBI (single combined feed: press releases, circulars, orders) -----
    {   # CONFIRMED
        "key": "sebi_all", "source": "sebi", "category": "SEBI",
        "type": "rss", "url": "https://www.sebi.gov.in/sebirss.xml",
    },

    # ---- RBI ----------------------------------------------------------------
    {   # CONFIRMED
        "key": "rbi_press", "source": "rbi", "category": "Press Releases",
        "type": "rss", "url": "https://www.rbi.org.in/pressreleases_rss.xml",
    },
    {   # CONFIRMED
        "key": "rbi_notif", "source": "rbi", "category": "Notifications",
        "type": "rss", "url": "https://www.rbi.org.in/notifications_rss.xml",
    },

    # ---- BSE (announcements / notices) -------------------------------------
    {   # NEEDS-CONFIRM: verify against https://beta.bseindia.com/rss-feed.html
        "key": "bse_notices", "source": "bse", "category": "Notices",
        "type": "rss", "url": "https://www.bseindia.com/data/xml/notices.xml",
    },

    # ---- NSE (corporate announcements) -------------------------------------
    {   # NEEDS-CONFIRM: NSE is bot-protected -- may need browser-like headers /
        # a primed cookie. Verify against https://www.nseindia.com/static/rss-feed
        "key": "nse_announcements", "source": "nse", "category": "Announcements",
        "type": "rss",
        "url": "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml",
    },

    # ---- AMFI (news / circulars -- NAV feeds intentionally excluded) --------
    {   # CONFIRMED against the live DOM 2026-08-13. The circulars page is a
        # Next.js SSR app (data is in the server HTML, so requests+bs4 works);
        # rows use hashed MUI classes, so we target circular PDFs by href
        # instead. Each row's first link is the circular; the rest are
        # "Attachment N ..." sub-files we drop. The date lives only in the file
        # name (e.g. "...dt. 31-Jan-24.pdf"), so we pull it from the URL.
        "key": "amfi_circulars", "source": "amfi", "category": "Circulars",
        "type": "scrape",
        "url": "https://www.amfiindia.com/circulars",
        "row_selector": 'a[href*="/downloads/circulars/"]',  # node IS the anchor
        "exclude_title_regex": r"^\s*Attachment\b",
        "date_from_url_regex": r"dt\.?\s*(\d{1,2}[-\s][A-Za-z]{3}[-\s]\d{2,4})",
        "date_selector": None,
    },

    # ---- IRDAI (no native RSS -- Liferay portlet table) --------------------
    {   # CONFIRMED against the live DOM 2026-08-13 (21 rows resolved).
        # Columns: Short Description (title) | Sub Title (doc-detail link) |
        #          Last Updated (DD-MM-YYYY). Dates are day-first (see ingest).
        "key": "irdai_circulars", "source": "irdai", "category": "Circulars",
        "type": "scrape",
        "url": "https://irdai.gov.in/circulars",
        # tbody-independent: BeautifulSoup/lxml don't synthesize <tbody> like a
        # browser does. The header row is skipped automatically (it has no
        # td.table-col-shortDesc / td.table-col-subTitle cell).
        "row_selector": "table.table-striped tr",
        "link_selector": "td.table-col-subTitle a",   # stable /document-detail landing page
        "title_selector": "td.table-col-shortDesc",    # the actual circular title
        "date_selector": "td.table-col-lastUpdated",
    },

    # ---- PFRDA (no native RSS -- Liferay asset-publisher list) --------------
    {   # CONFIRMED against the live DOM 2026-08-13 (20 rows resolved).
        # /regulatory-framework/circulars redirects to .../active-circulars.
        # Note: the list shows no per-item date (published_date stays NULL) and
        # the source truncates long titles with an ellipsis; both link through
        # to the full document.
        "key": "pfrda_circulars", "source": "pfrda", "category": "Circulars",
        "type": "scrape",
        "url": "https://www.pfrda.org.in/regulatory-framework/circulars/active-circulars",
        "row_selector": "ul.article-list li.article-entry",
        "link_selector": "h3.article-title a",
        "title_selector": "h3.article-title a",
        "date_selector": None,
    },
]


# ---------------------------------------------------------------------------
# Runtime / DB config (from environment)
# ---------------------------------------------------------------------------
DB = {
    "host": os.getenv("SH_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("SH_DB_PORT", "3306")),
    "user": os.getenv("SH_DB_USER", "root"),
    "password": os.getenv("SH_DB_PASSWORD", ""),
    "database": os.getenv("SH_DB_NAME", "safehands"),
    "charset": "utf8mb4",
}

# Networking
HTTP_TIMEOUT = int(os.getenv("SH_HTTP_TIMEOUT", "30"))

# TLS verification. Default on. Behind a corporate proxy / MITM, point
# SH_CA_BUNDLE at the proxy's CA .pem (preferred). Only as a last resort on a
# trusted network set SH_VERIFY_SSL=0 to skip verification.
_CA_BUNDLE = os.getenv("SH_CA_BUNDLE", "").strip()
_VERIFY_FLAG = os.getenv("SH_VERIFY_SSL", "1").strip().lower() not in ("0", "false", "no")
# VERIFY is what requests' `verify=` expects: a CA-bundle path, or True/False.
VERIFY = _CA_BUNDLE if _CA_BUNDLE else _VERIFY_FLAG
MAX_WORKERS = int(os.getenv("SH_MAX_WORKERS", "8"))
USER_AGENT = os.getenv(
    "SH_USER_AGENT",
    "SafeHands-RegBot/1.0 (+compliance feed; contact: admin@safehands.in)",
)

# How often the scheduler re-polls, in minutes (used by `main.py --schedule`)
POLL_INTERVAL_MINUTES = int(os.getenv("SH_POLL_INTERVAL_MINUTES", "60"))

# Safety cap: keep at most this many newest items per single feed per run
MAX_ITEMS_PER_FEED = int(os.getenv("SH_MAX_ITEMS_PER_FEED", "100"))
