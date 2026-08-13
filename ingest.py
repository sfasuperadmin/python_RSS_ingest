"""
Core ingestion logic: fetch every source concurrently, normalize to a common
shape, dedupe by content_hash, and upsert into MySQL `regulatory_updates`.

The Yii backend only reads that table; this service is the sole writer.
"""

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, unquote

import feedparser
import pymysql
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

import config

log = logging.getLogger("rss_ingest")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _hash(source, url, title):
    """Stable dedupe key. URL is the strongest signal; title backs it up."""
    basis = "|".join([source or "", (url or "").strip(), (title or "").strip()])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _parse_date(value, struct_time=None, dayfirst=False):
    """Best-effort -> 'YYYY-MM-DD HH:MM:SS' or None. Never raises.

    dayfirst=True for Indian scrape sources, which use DD-MM-YYYY (e.g. IRDAI's
    'Last Updated' column). RSS feeds pass an unambiguous struct_time instead.
    """
    if struct_time:
        try:
            return datetime(*struct_time[:6]).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    if value:
        try:
            return dateparser.parse(value, fuzzy=True, dayfirst=dayfirst).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    return None


def _clean(text, limit=None):
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text[:limit] if limit else text


def _normalize(source_cfg, *, title, url, ref_no, summary, published_date, raw):
    """Build one row dict matching the regulatory_updates columns."""
    title = _clean(title, 600)
    if not title:
        return None
    return {
        "source": source_cfg["source"],
        "category": _clean(source_cfg.get("category"), 60),
        "title": title,
        "ref_no": _clean(ref_no, 200),
        "published_date": published_date,
        "source_url": _clean(url, 1000),
        "summary": _clean(summary, 8000),
        "raw_json": _clean(raw, 60000),
        "content_hash": _hash(source_cfg["source"], url, title),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------
def _http_get(url):
    if config.VERIFY is False:
        # silence the noisy per-request warning when verification is disabled
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
    resp = requests.get(
        url,
        timeout=config.HTTP_TIMEOUT,
        headers={"User-Agent": config.USER_AGENT, "Accept": "*/*"},
        verify=config.VERIFY,
    )
    resp.raise_for_status()
    return resp


def fetch_rss(s):
    # Fetch bytes ourselves so we control the User-Agent (some feeds 403 the
    # default python-feedparser agent), then hand the content to feedparser.
    try:
        content = _http_get(s["url"]).content
    except Exception as e:
        log.warning("[%s] HTTP fetch failed: %s", s["key"], e)
        content = s["url"]  # let feedparser try directly as a fallback

    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        log.warning("[%s] feed did not parse: %s", s["key"], getattr(parsed, "bozo_exception", ""))

    rows = []
    for e in parsed.entries[: config.MAX_ITEMS_PER_FEED]:
        row = _normalize(
            s,
            title=e.get("title"),
            url=e.get("link"),
            ref_no=e.get("id") or e.get("guid"),
            summary=e.get("summary") or e.get("description"),
            published_date=_parse_date(e.get("published") or e.get("updated"),
                                       e.get("published_parsed") or e.get("updated_parsed")),
            raw=str(e)[:60000],
        )
        if row:
            rows.append(row)
    return rows


def fetch_scrape(s):
    resp = _http_get(s["url"])
    soup = BeautifulSoup(resp.text, "lxml")
    rows = []
    seen_local = set()
    excl = re.compile(s["exclude_title_regex"]) if s.get("exclude_title_regex") else None
    url_date = re.compile(s["date_from_url_regex"]) if s.get("date_from_url_regex") else None
    for node in soup.select(s["row_selector"])[: config.MAX_ITEMS_PER_FEED * 2]:
        # the matched node may itself be the anchor (row_selector = "a[...]"),
        # otherwise find the link inside it
        if node.name == "a" and node.get("href"):
            link = node
        else:
            link = node.select_one(s.get("link_selector") or "a")
        if not link:
            continue
        href = link.get("href")
        if not href:
            continue
        href = urljoin(s["url"], href)

        if s.get("title_selector"):
            tnode = node.select_one(s["title_selector"])
            title = tnode.get_text(strip=True) if tnode else None
        else:
            title = link.get_text(strip=True)
        if not title:
            continue
        # drop sub-rows we don't want as standalone items (e.g. AMFI "Attachment N")
        if excl and excl.search(title):
            continue

        published = None
        if s.get("date_selector"):
            dnode = node.select_one(s["date_selector"])
            if dnode:
                # Indian regulator pages use DD-MM-YYYY -> day-first
                published = _parse_date(dnode.get_text(strip=True), dayfirst=True)
        # some sources carry the date only in the file name / URL (e.g. AMFI
        # "dt. 31-Jan-24"). Decode %20 etc. first so the pattern can match.
        if not published and url_date:
            m = url_date.search(unquote(href))
            if m:
                published = _parse_date(m.group(1), dayfirst=True)

        row = _normalize(s, title=title, url=href, ref_no=None,
                         summary=None, published_date=published, raw=str(node)[:60000])
        if row and row["content_hash"] not in seen_local:
            seen_local.add(row["content_hash"])
            rows.append(row)
        if len(rows) >= config.MAX_ITEMS_PER_FEED:
            break
    return rows


def fetch_one(s):
    try:
        rows = fetch_rss(s) if s["type"] == "rss" else fetch_scrape(s)
        log.info("[%s] %d item(s)", s["key"], len(rows))
        return rows
    except Exception as e:
        log.error("[%s] fetch failed: %s", s["key"], e)
        return []


def collect(sources=None):
    sources = sources or config.SOURCES
    all_rows = []
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_one, s): s for s in sources}
        for fut in as_completed(futures):
            all_rows.extend(fut.result())
    # de-dupe within this run (a URL can appear in two feeds)
    unique = {}
    for r in all_rows:
        unique[r["content_hash"]] = r
    return list(unique.values())


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
UPSERT_SQL = (
    "INSERT INTO regulatory_updates "
    "(source, category, title, ref_no, published_date, source_url, summary, "
    " raw_json, content_hash, fetched_at) "
    "VALUES (%(source)s, %(category)s, %(title)s, %(ref_no)s, %(published_date)s, "
    " %(source_url)s, %(summary)s, %(raw_json)s, %(content_hash)s, %(fetched_at)s) "
    "ON DUPLICATE KEY UPDATE "
    " title=VALUES(title), category=VALUES(category), ref_no=VALUES(ref_no), "
    " published_date=VALUES(published_date), source_url=VALUES(source_url), "
    " summary=VALUES(summary), raw_json=VALUES(raw_json), fetched_at=VALUES(fetched_at)"
)


def upsert(rows):
    if not rows:
        log.info("nothing to upsert")
        return 0
    conn = pymysql.connect(**config.DB)
    try:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, rows)
        conn.commit()
        log.info("upserted %d row(s)", len(rows))
        return len(rows)
    finally:
        conn.close()


def run_once():
    rows = collect()
    return upsert(rows)
