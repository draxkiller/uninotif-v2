#!/usr/bin/env python3
"""
Pondicherry University — Telegram Notification Bot  v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✦ All 7 categories monitored
✦ Multi-recipient (users)
✦ Error alerts to admin only
✦ Daily heartbeat
✦ 5-minute checks via GitHub Actions
✦ AI summary via Google Gemini Flash (optional)
"""

import html, mimetypes, os, re, json, time, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────
# CONFIG  (all values come from GitHub Secrets)
# ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
if not re.match(r'^\d+:[\w-]{35,}$', TELEGRAM_TOKEN):
    raise ValueError(
        "TELEGRAM_BOT_TOKEN looks invalid — expected format: <bot_id>:<35+ char token>. "
        "Check your GitHub secret."
    )
# Comma-separated user chat IDs  e.g.  "123456789,987654321"
# First ID = admin
_raw_ids         = os.environ.get("TELEGRAM_CHAT_IDS", os.environ.get("TELEGRAM_CHAT_ID", ""))
CHAT_IDS         = [c.strip() for c in _raw_ids.split(",") if c.strip()]
ADMIN_CHAT_ID    = CHAT_IDS[0] if CHAT_IDS else ""

BASE_URL         = "https://www.pondiuni.edu.in"
NOTIF_URL        = f"{BASE_URL}/all-notifications/"
SEEN_FILE        = "seen.json"
HEARTBEAT_FILE   = "heartbeat.json"

DDE_BASE_URL = "https://dde.pondiuni.edu.in"

# DDE (Directorate of Distance Education) listing pages to scrape.
# Each entry is (url, category_label).
DDE_LIST_PAGES = [
    (f"{DDE_BASE_URL}/notification-all-announcements-list/",      "DDE Announcements 📢"),
    (f"{DDE_BASE_URL}/notification-all-exam-notification-list/",  "DDE Exam Notifications 📋"),
    (f"{DDE_BASE_URL}/exam-results-2/",                           "DDE Exam Results 📊"),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TAB_SLUGS = {
    "Circulars":  ("Circulars",           "📋"),
    "News":       ("News & Announcements", "📰"),
    "PhD":        ("Ph.D Notifications",  "🎓"),
    "Events":     ("Events",              "🗓️"),
    "Admission":  ("Admission",           "🏫"),
    "Careers":    ("Careers",             "💼"),
    "Tenders":    ("Tenders",             "📝"),
}

# Extra pages to scrape for section-specific notifications.
# These are WordPress section pages whose child links are treated as notifications.
# Note: /admission/ and /directorate-of-distance-education/ were removed — both
# return 404.  Admission posts are covered by the WP REST API; distance-education
# notifications are covered by DDE_LIST_PAGES above.
EXTRA_SECTIONS: list[tuple[str, str]] = []

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ─────────────────────────────────────────────────────────────
# AI SUMMARY CONFIG  (optional — set GEMINI_API_KEY secret)
# ─────────────────────────────────────────────────────────────
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
# Set ENABLE_AI_SUMMARY=false to disable summaries without removing the key
ENABLE_AI_SUMMARY  = os.environ.get("ENABLE_AI_SUMMARY", "true").lower() not in ("false", "0", "no")
AI_SUMMARY_ENABLED = bool(GEMINI_API_KEY) and ENABLE_AI_SUMMARY

# Number of most-recently-notified entries to re-send (0 = disabled, max 10)
RESEND_LAST = min(10, max(0, int(os.environ.get("RESEND_LAST", "0") or "0")))

_gemini_client = None   # lazily initialised

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        from google import genai  # noqa: PLC0415
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client

# ─────────────────────────────────────────────────────────────
# SEEN / HEARTBEAT STORE
# ─────────────────────────────────────────────────────────────
def load_json(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_json(path: str, data: dict):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def _abs(href: str) -> str:
    """Convert a relative URL to absolute using BASE_URL."""
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


def _fmt_wp_date(date_str: str) -> str:
    """Format a WP REST API date string (2024-01-15T10:30:00) to readable form."""
    try:
        dt = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.strftime("%d %b %Y")
    except Exception:
        return date_str


def fetch_all_notifications(seen_ids: set | None = None) -> list[dict]:
    results = _try_wp_rest_api(seen_ids)
    if results is not None:
        print(f"  [API]  {len(results)} notifications via WP REST API")
    else:
        results = _scrape_html()
        print(f"  [HTML] {len(results)} notifications via HTML scrape")

    # Also scrape admission and distance-education section pages.
    existing_links = {r["link"] for r in results}
    for section_url, category in EXTRA_SECTIONS:
        extras = _scrape_section_links(section_url, category)
        for item in extras:
            if item["id"] not in (seen_ids or set()) and item["link"] not in existing_links:
                results.append(item)
                existing_links.add(item["link"])

    # Scrape DDE (Directorate of Distance Education) listing pages.
    for dde_url, category in DDE_LIST_PAGES:
        dde_items = _scrape_dde_list_page(dde_url, category)
        for item in dde_items:
            if item["id"] not in (seen_ids or set()) and item["link"] not in existing_links:
                results.append(item)
                existing_links.add(item["link"])

    return results


def _try_wp_rest_api(seen_ids: set | None = None) -> list[dict] | None:
    """Return a list of notifications from the WP REST API, or None if unavailable."""
    all_items = []
    api_failed = False
    for page in range(1, 6):
        url = (
            f"{BASE_URL}/wp-json/wp/v2/university_news"
            f"?per_page=50&page={page}&orderby=date&order=desc&_embed=true"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 400:
                break
            if r.status_code != 200:
                print(f"  WP API returned HTTP {r.status_code} — falling back to HTML scrape")
                api_failed = True
                break
            items = r.json()
            if not items:
                break
            for item in items:
                cat_name, cat_emoji = "General", "🔔"
                try:
                    terms = item["_embedded"]["wp:term"][0]
                    if terms:
                        raw = terms[0]["name"]
                        for key, (name, emoji) in TAB_SLUGS.items():
                            if key.lower() in raw.lower() or name.lower() in raw.lower():
                                cat_name, cat_emoji = name, emoji
                                break
                        else:
                            cat_name = raw
                            print(f"  [API] Unrecognized category: {raw!r}")
                except Exception:
                    pass
                content_html = item.get("content", {}).get("rendered", "")
                pdf_urls     = _pdfs_from_html(content_html)
                entry = {
                    "id":        str(item["id"]),
                    "title":     BeautifulSoup(item["title"]["rendered"], "html.parser").get_text(strip=True),
                    "link":      item["link"],
                    "category":  f"{cat_name} {cat_emoji}",
                    "issued_by": "",
                    "date":      _fmt_wp_date(item.get("date", "")),
                }
                if content_html:
                    entry["body_html"] = content_html
                if pdf_urls:
                    entry["pdf_urls"] = pdf_urls
                all_items.append(entry)
            # If every item on this page is already known, older pages will be too
            if seen_ids and all(str(item["id"]) in seen_ids for item in items):
                break
        except Exception as e:
            print(f"  WP API page {page} error: {e}")
            if not all_items:
                api_failed = True
            break
    return None if api_failed else all_items


def _scrape_html() -> list[dict]:
    try:
        r = requests.get(NOTIF_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  Failed to fetch page: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for tab_id, (cat_name, cat_emoji) in TAB_SLUGS.items():
        container = (
            soup.find(id=tab_id) or
            soup.find(id=tab_id.lower()) or
            soup.find("div", {"data-id": tab_id})
        )
        if container:
            _extract_rows(container, f"{cat_name} {cat_emoji}", results)

    if not results:
        _extract_rows(soup, "General 🔔", results)

    if not results:
        print("  [HTML] No table rows found — trying link scan fallback")
        seen_links_fb: set = set()
        for a in soup.find_all("a", href=True):
            href = _abs(a["href"])
            title = a.get_text(strip=True)
            if not title or len(title) < 10:
                continue
            if href in seen_links_fb:
                continue
            if any(skip in href for skip in ["#", "javascript", "mailto", "facebook", "twitter"]):
                continue
            seen_links_fb.add(href)
            results.append({
                "id": href, "title": title, "link": href,
                "category": "General 🔔", "issued_by": "", "date": "",
            })

    seen_links, deduped = set(), []
    for n in results:
        if n["link"] not in seen_links:
            seen_links.add(n["link"])
            deduped.append(n)
    return deduped


def _extract_rows(container, category: str, out: list):
    for row in container.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        link_tag = cells[0].find("a", href=True)
        if not link_tag:
            continue
        href  = _abs(link_tag["href"])
        title = link_tag.get_text(strip=True)
        if not title:
            continue
        issued_by = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        date_str  = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        out.append({
            "id": href, "title": title, "link": href,
            "category": category, "issued_by": issued_by, "date": date_str,
        })


def _scrape_section_links(section_url: str, category: str) -> list[dict]:
    """Scrape a WordPress section page (e.g. /admission/) for child notification links.

    Extracts links whose href starts with the section URL, so only actual
    child pages/posts are returned — navigation and footer links are excluded.
    """
    # Minimum number of characters a link title must have to be considered a notification.
    _MIN_TITLE_LEN = 10
    # Href substrings that indicate non-content links (JS actions, social sites, etc.)
    _SKIP_HREF = ("javascript:", "mailto:", "facebook.com", "twitter.com")

    try:
        r = requests.get(section_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  Failed to fetch section {section_url}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    # Strip navigation / decorative regions
    for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
        tag.decompose()
    for tag in soup.find_all(True, {"class": re.compile(
            r"nav|menu|header|footer|sidebar|breadcrumb|widget", re.I)}):
        tag.decompose()

    content = (
        soup.find("main")
        or soup.find("div", {"class": re.compile(r"entry[._-]content|post[._-]content|content[._-]area|main[._-]content", re.I)})
        or soup
    )

    section_base = section_url.rstrip("/")
    results: list[dict] = []
    seen_links: set = set()

    for a in content.find_all("a", href=True):
        href  = _abs(a["href"])
        title = a.get_text(strip=True)
        if not title or len(title) < _MIN_TITLE_LEN:
            continue
        if href in seen_links:
            continue
        if any(skip in href for skip in _SKIP_HREF):
            continue
        if "pondiuni.edu.in" not in href:
            continue
        # Only include links that are children of this section (not nav links, etc.)
        if not href.rstrip("/").startswith(section_base + "/"):
            continue
        seen_links.add(href)
        results.append({
            "id":        href,
            "title":     title,
            "link":      href,
            "category":  category,
            "issued_by": "",
            "date":      "",
        })

    print(f"  [Section] {len(results)} link(s) found under {section_url}")
    return results


def _abs_dde(href: str) -> str:
    """Convert a relative URL to absolute using DDE_BASE_URL."""
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return DDE_BASE_URL + href
    return DDE_BASE_URL + "/" + href


def _scrape_dde_list_page(page_url: str, category: str) -> list[dict]:
    """Scrape a DDE listing page for notification links.

    Handles both table-based layouts (rows with title/date cells) and
    generic link-list layouts common on WordPress-based university sites.
    Only links hosted on dde.pondiuni.edu.in are returned.
    """
    _MIN_TITLE_LEN = 10
    _SKIP_HREF = ("javascript:", "mailto:", "facebook.com", "twitter.com", "instagram.com")

    try:
        r = requests.get(page_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  Failed to fetch DDE page {page_url}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    # Strip navigation / decorative regions
    for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
        tag.decompose()
    for tag in soup.find_all(True, {"class": re.compile(
            r"nav|menu|header|footer|sidebar|breadcrumb|widget", re.I)}):
        tag.decompose()

    content = (
        soup.find("main")
        or soup.find("div", {"class": re.compile(
            r"entry[._-]content|post[._-]content|content[._-]area|main[._-]content", re.I)})
        or soup
    )

    results: list[dict] = []
    seen_links: set = set()

    # ── 1. Table rows (title cell + optional issued-by + date cells) ──
    for row in content.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        link_tag = cells[0].find("a", href=True)
        if not link_tag:
            continue
        href  = _abs_dde(link_tag["href"])
        title = link_tag.get_text(strip=True)
        if not title or len(title) < _MIN_TITLE_LEN:
            continue
        if href in seen_links:
            continue
        if any(skip in href for skip in _SKIP_HREF):
            continue
        if "pondiuni.edu.in" not in href:
            continue
        issued_by = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        date_str  = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        seen_links.add(href)
        results.append({
            "id":        href,
            "title":     title,
            "link":      href,
            "category":  category,
            "issued_by": issued_by,
            "date":      date_str,
        })

    # ── 2. Generic link scan (for list/card layouts) ──────────────
    for a in content.find_all("a", href=True):
        href  = _abs_dde(a["href"])
        title = a.get_text(strip=True)
        if not title or len(title) < _MIN_TITLE_LEN:
            continue
        if href in seen_links:
            continue
        if any(skip in href for skip in _SKIP_HREF):
            continue
        if "pondiuni.edu.in" not in href:
            continue
        # Skip links that are just the listing page itself
        if href.rstrip("/") == page_url.rstrip("/"):
            continue
        seen_links.add(href)
        results.append({
            "id":        href,
            "title":     title,
            "link":      href,
            "category":  category,
            "issued_by": "",
            "date":      "",
        })

    print(f"  [DDE] {len(results)} notification(s) found on {page_url}")
    return results

# ─────────────────────────────────────────────────────────────
# PDF EXTRACTION + DOWNLOAD
# ─────────────────────────────────────────────────────────────
def _pdfs_from_html(html: str) -> list[str]:
    """Extract all PDF and image attachment URLs found in an HTML string (deduplicated, order-preserved)."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()

    def _add(url: str):
        abs_url = _abs(url)
        if abs_url not in seen:
            seen.add(abs_url)
            found.append(abs_url)

    # 1. Direct <a href="...pdf/image">
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if re.search(r"\.(pdf|jpg|jpeg|png|gif|webp)(\?|$)", href, re.I):
            _add(href)
    # 2. <embed>, <iframe>, <object>
    for tag in soup.find_all(["embed", "iframe", "object"]):
        src = tag.get("src") or tag.get("data") or ""
        if re.search(r"\.(pdf|jpg|jpeg|png|gif|webp)(\?|$)", src, re.I):
            _add(src)
    # 3. <img src="...jpeg/png"> — scanned notices are often embedded as images
    # Note: .gif is intentionally excluded here; GIF files found in <img> tags are
    # almost always decorative UI elements (spinners, icons).  Genuine GIF attachments
    # are still captured above via <a href="...gif"> links.
    for tag in soup.find_all("img"):
        src = (tag.get("src") or "").strip()
        if re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", src, re.I):
            _add(src)
    # 4. JS/text patterns (PDF-specific)
    for pat in [
        r'ViewerJS/#(?:https?:)?([^\s"\'<]+\.pdf[^\s"\'<]*)',
        r'file=([^\s&"\'<]+\.pdf[^\s&"\'<]*)',
        r'["\']([^"\']*?/(?:uploads|files|documents|notices|notification|download|media|pdf|attachments)[^"\']*?\.pdf)["\']',
    ]:
        for m in re.finditer(pat, html, re.I):
            c = m.group(1)
            if len(c) > 8:  # sanity-check: skip trivially short matches (e.g. ".pdf" alone)
                _add(c)
    return found


def choose_primary_pdf_url(urls: list[str], title: str = "") -> str | None:
    """Choose the single most likely *primary* PDF from a list of candidate URLs.

    Scoring is deterministic:
    - Boost: URL contains circular/notice/notification keywords.
    - Boost: direct ``.pdf`` link, university domain, WP uploads path.
    - Penalty: common secondary-attachment patterns (annex, form, brochure, etc.).
    - Tiny bonus: URL filename shares long words with the post title.
    - Tie-break: original list order (first URL wins).
    """
    if not urls:
        return None
    if len(urls) == 1:
        return urls[0]

    _BOOST_TERMS = ("circular", "notification", "notice", "order", "corrigendum")
    _PENALTY_TERMS = (
        "annex", "appendix", "attachment", "form", "application",
        "brochure", "prospectus", "timetable", "schedule",
        "guidelines", "instruction", "logo", "favicon",
    )

    # Pre-compute title words once; reused inside _score() for each URL
    title_words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 6]

    def _score(url: str) -> int:
        s = 0
        lu = url.lower()

        # Strongly prefer actual .pdf links
        if re.search(r"\.pdf(\?|$)", url, re.I):
            s += 50

        # Prefer university-hosted uploads
        if "pondiuni.edu.in" in lu:
            s += 10
        if "/wp-content/uploads/" in lu:
            s += 10

        # Boost "main document" keywords (award only once)
        if any(k in lu for k in _BOOST_TERMS):
            s += 25

        # Penalise secondary attachment patterns
        for k in _PENALTY_TERMS:
            if k in lu:
                s -= 25

        # Tiny bonus when filename shares long words with the post title
        for w in title_words:
            if w in lu:
                s += 2

        return s

    # max() returns the first maximum element on ties, preserving list order
    return max(urls, key=_score)


def _sort_pdf_urls(urls: list[str], title: str = "") -> list[str]:
    """Return all PDF/image URLs sorted by relevance (primary first).

    Uses the same scoring as choose_primary_pdf_url but keeps every URL instead
    of discarding all but the top-ranked one.  Duplicate URLs are removed while
    preserving the sorted order.
    """
    if not urls:
        return []

    _BOOST_TERMS = ("circular", "notification", "notice", "order", "corrigendum")
    _PENALTY_TERMS = (
        "annex", "appendix", "attachment", "form", "application",
        "brochure", "prospectus", "timetable", "schedule",
        "guidelines", "instruction", "logo", "favicon",
    )
    title_words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 6]

    def _score(url: str) -> int:
        s = 0
        lu = url.lower()
        if re.search(r"\.pdf(\?|$)", url, re.I):
            s += 50
        if "pondiuni.edu.in" in lu:
            s += 10
        if "/wp-content/uploads/" in lu:
            s += 10
        if any(k in lu for k in _BOOST_TERMS):
            s += 25
        for k in _PENALTY_TERMS:
            if k in lu:
                s -= 25
        for w in title_words:
            if w in lu:
                s += 2
        return s

    seen: set[str] = set()
    deduped = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return sorted(deduped, key=_score, reverse=True)


def get_pdf_urls(detail_url: str) -> list[str]:
    try:
        r = requests.get(detail_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Remove nav/header/footer so their PDFs don't pollute results
        for tag in soup.find_all(["nav", "header", "footer", "script", "style"]):
            tag.decompose()
        for tag in soup.find_all(True, {"class": re.compile(
                r"nav|menu|header|footer|sidebar|breadcrumb|widget", re.I)}):
            tag.decompose()

        # Try main content area first, fall back to full page
        content = (
            soup.find("div", {"class": re.compile(r"entry.content|post.content|main.content|content.area|single.content", re.I)})
            or soup.find("main")
            or soup.find("article")
            or soup
        )

        return _pdfs_from_html(str(content))

    except Exception as e:
        print(f"    PDF extraction error: {e}")
    return []


def _detect_file_ext(first_chunk: bytes) -> str | None:
    """Return the file extension for a known file type based on magic bytes, or None."""
    if first_chunk.startswith(b"%PDF"):
        return ".pdf"
    if first_chunk.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if first_chunk.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if first_chunk.startswith(b"GIF8"):
        return ".gif"
    if (first_chunk.startswith(b"RIFF") and len(first_chunk) >= 12
            and first_chunk[8:12] == b"WEBP"):
        return ".webp"
    return None


def _tmp_attachment_path(url: str, ext: str = ".bin") -> str:
    """Return a deterministic /tmp path for an attachment URL."""
    uid = hashlib.md5(url.encode()).hexdigest()[:10]
    return f"/tmp/pu_{uid}{ext}"


def download_pdf(pdf_url: str, _retry: bool = True) -> str | None:
    """
    Download a PDF or image attachment from pdf_url to a temp file.
    Validates using magic bytes instead of Content-Type header.

    Supported types: PDF (%PDF), JPEG, PNG, GIF, WEBP.

    If the response is an HTML viewer/redirect page rather than a raw file,
    the HTML is parsed for a direct attachment link and the download is
    retried once with that URL.
    """
    local = _tmp_attachment_path(pdf_url)   # .bin while downloading; renamed after detection
    try:
        with requests.get(pdf_url, headers=HEADERS, timeout=60, stream=True) as r:
            if r.status_code != 200:
                print(f"    Attachment download HTTP {r.status_code} — skipping")
                return None
            size        = 0
            first_chunk = None
            chunks      = []
            detected_ext = None
            with open(local, "wb") as f:
                for chunk in r.iter_content(8192):
                    if first_chunk is None:
                        first_chunk = chunk
                        detected_ext = _detect_file_ext(chunk)
                    f.write(chunk)
                    size += len(chunk)
                    if size > 49 * 1024 * 1024:
                        print("    Attachment too large (>49 MB) — skipping")
                        Path(local).unlink(missing_ok=True)
                        return None
                    # Buffer HTML content (up to 512 KB) so we can extract
                    # a direct attachment URL if the magic-bytes check later fails.
                    if _retry and detected_ext is None and size <= 512 * 1024:
                        chunks.append(chunk)

            # Validate using magic bytes — don't trust Content-Type
            if not first_chunk or detected_ext is None:
                Path(local).unlink(missing_ok=True)
                # The URL may point to an HTML viewer wrapping the real file.
                # Try to extract a direct link from the page and retry once.
                if _retry and chunks:
                    html_content = b"".join(chunks).decode("utf-8", errors="replace")
                    candidates   = _pdfs_from_html(html_content)
                    print(f"    Viewer page: {len(candidates)} attachment candidate(s) found")
                    direct_url   = choose_primary_pdf_url(candidates)
                    if direct_url and direct_url != pdf_url:
                        print(f"    Viewer page detected — retrying with primary → {direct_url[:80]}")
                        return download_pdf(direct_url, _retry=False)
                print("    Not a valid PDF or image (bad magic bytes) — skipping")
                return None

        # Rename to the correct extension now that we know the file type
        final = _tmp_attachment_path(pdf_url, detected_ext)
        Path(local).rename(final)

        file_size = Path(final).stat().st_size
        if file_size <= 512:
            print(f"    Attachment too small ({file_size} bytes) — skipping")
            Path(final).unlink(missing_ok=True)
            return None

        print(f"    Attachment downloaded OK ({file_size // 1024} KB, type: {detected_ext})")
        return final

    except Exception as e:
        print(f"    Attachment download error: {e}")
        Path(local).unlink(missing_ok=True)
        return None


# ─────────────────────────────────────────────────────────────
# AI SUMMARY
# ─────────────────────────────────────────────────────────────
_AI_MAX_CHARS = 3000   # truncation limit fed to the model

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract plain text from a downloaded PDF using pdfplumber.

    Returns an empty string if extraction fails or pdfplumber is unavailable.
    Only the first 5 pages are read to keep latency low.
    """
    try:
        import pdfplumber  # noqa: PLC0415
        text_parts: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:5]:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts).strip()
    except Exception as e:
        print(f"    PDF text extraction error: {e}")
        return ""


def get_ai_summary(text: str) -> str:
    """Return a 2–3 sentence AI summary of a notification's text content.

    Returns an empty string on any error so the caller can fall back gracefully.
    """
    if not AI_SUMMARY_ENABLED:
        return ""
    text = text.strip()
    if not text or len(text) < 30:
        return ""
    try:
        truncated = text[:_AI_MAX_CHARS]
        client = _get_gemini_client()
        prompt = (
            "You are a helpful assistant for university students. "
            "Summarize the following university notification in 2-3 concise sentences. "
            "Focus on the key information (what, who, when, where). "
            "Reply with the summary only, no preamble.\n\n"
            f"{truncated}"
        )
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        summary  = (response.text or "").strip()
        if not summary:
            print("    AI summary: empty response from model")
            return ""
        print(f"    AI summary generated ({len(summary)} chars)")
        return summary
    except Exception as e:
        print(f"    AI summary error (skipping): {e}")
        return ""


def _tg_post(endpoint: str, chat_id: str, **kwargs) -> bool:
    """Post to Telegram with retry + rate-limit handling."""
    for attempt in range(3):
        try:
            r = requests.post(f"{TG_API}/{endpoint}", timeout=60, **kwargs)
            if r.ok:
                return True
            err = r.json().get("description", r.text)
            print(f"    TG {endpoint} attempt {attempt+1} failed ({chat_id}): {err}")
            if "Too Many Requests" in err:
                m = re.search(r"\d+", err)
                wait = (int(m.group()) if m else 5) + 1
                time.sleep(wait)
            elif "file" in err.lower() or "document" in err.lower():
                return False
            else:
                time.sleep(2)
        except Exception as e:
            print(f"    TG error: {e}")
            time.sleep(3)
    return False


def tg_text(chat_id: str, text: str) -> bool:
    return _tg_post("sendMessage", chat_id, json={
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    })


def tg_document_file(chat_id: str, path: str, caption: str) -> bool:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return _tg_post("sendDocument", chat_id,
            data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"document": (Path(path).name, f, mime)},
        )


def broadcast_text(text: str):
    """Send text to ALL configured chat IDs."""
    for cid in CHAT_IDS:
        tg_text(cid, text)
        time.sleep(0.5)


def broadcast_document_file(path: str, caption: str):
    for cid in CHAT_IDS:
        tg_document_file(cid, path, caption)
        time.sleep(0.5)


def tg_media_group_files(chat_id: str, paths: list[str], caption: str) -> bool:
    """Send 2–10 files as a single Telegram media group (album).

    The full caption (with HTML parse mode) is attached to the first item only,
    which is how Telegram's sendMediaGroup API works.
    """
    media = []
    files = {}
    handles: list = []
    try:
        for i, path in enumerate(paths):
            mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
            key = f"file{i}"
            item: dict = {"type": "document", "media": f"attach://{key}"}
            if i == 0:
                item["caption"] = caption[:1024]
                item["parse_mode"] = "HTML"
            media.append(item)
            fh = open(path, "rb")
            handles.append(fh)
            files[key] = (Path(path).name, fh, mime)
        return _tg_post("sendMediaGroup", chat_id,
            data={"chat_id": chat_id, "media": json.dumps(media)},
            files=files,
        )
    finally:
        for fh in handles:
            fh.close()


def broadcast_media_group_files(paths: list[str], caption: str):
    """Broadcast a media group to all configured chat IDs."""
    for cid in CHAT_IDS:
        tg_media_group_files(cid, paths, caption)
        time.sleep(0.5)


def alert_admin(text: str):
    """Send error/status messages to admin only."""
    if ADMIN_CHAT_ID:
        tg_text(ADMIN_CHAT_ID, f"⚠️ <b>Bot Alert</b>\n\n{text}")

# ─────────────────────────────────────────────────────────────
# MESSAGE FORMATTING
# ─────────────────────────────────────────────────────────────
def build_caption(n: dict, summary: str = "") -> str:
    summary_block = f"\n🤖 <b>AI Summary:</b>\n{summary}\n" if summary else ""
    category = n.get("category", "General")
    # Identify DDE notifications by checking against the known DDE category labels
    _dde_categories = {cat for _, cat in DDE_LIST_PAGES}
    is_dde = category in _dde_categories
    institution = (
        "🏛 <b>Pondicherry University — DDE</b>\n<i>(Distance Education)</i>"
        if is_dde else
        "🏛 <b>Pondicherry University</b>"
    )
    return (
        f"🔔 <b>NEW NOTIFICATION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{institution}\n\n"
        f"📁 <b>Category :</b> {category}\n"
        f"📄 <b>Title    :</b> <code>{html.escape(n['title'])}</code>\n"
        f"🏢 <b>Issued by:</b> {n.get('issued_by') or '—'}\n"
        f"📅 <b>Date     :</b> {n.get('date') or '—'}"
        f"{summary_block}\n"
        f"🔗 <a href=\"{n['link']}\">Open on Website ↗</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

# ─────────────────────────────────────────────────────────────
# DELIVER ONE NOTIFICATION
# ─────────────────────────────────────────────────────────────
def deliver(n: dict):
    link    = n["link"]

    # Collect PDF URL(s) for this notification, sorted by relevance (primary first).
    # Prefer PDF URLs extracted from API content (no extra HTTP request).
    # Fall back to direct-link detection, then full page fetch.
    if "pdf_urls" in n:
        candidates = n["pdf_urls"]
        print(f"    {len(candidates)} PDF candidate(s) from API content")
        pdf_urls = _sort_pdf_urls(candidates, title=n.get("title", ""))
        if pdf_urls:
            print(f"    Primary PDF → {pdf_urls[0][:80]}")
    elif "pdf_url" in n:
        pdf_urls = [n["pdf_url"]]
        print("    PDF URL from API content")
    elif re.search(r'\.(pdf|jpg|jpeg|png|gif|webp)(\?|$)', link, re.I):
        pdf_urls = [link]
        print("    Direct attachment link detected — skipping page fetch")
    else:
        candidates = get_pdf_urls(link)
        print(f"    {len(candidates)} PDF candidate(s) found on page")
        pdf_urls = _sort_pdf_urls(candidates, title=n.get("title", ""))
        if pdf_urls:
            print(f"    Primary PDF → {pdf_urls[0][:80]}")

    pdf_paths: list[str] = []
    failed_urls: list[str] = []
    try:
        for pdf_url in pdf_urls:
            print(f"    PDF found → {pdf_url[:80]}")
            pdf_path = download_pdf(pdf_url)
            if pdf_path:
                pdf_paths.append(pdf_path)
            else:
                print("    PDF download failed — will include link in message")
                failed_urls.append(pdf_url)

        # ── AI summary ──────────────────────────────────────────
        # Priority: PDF text (richest) → API body HTML → skip
        summary = ""
        if AI_SUMMARY_ENABLED:
            raw_text = ""
            if pdf_paths:
                raw_text = extract_text_from_pdf(pdf_paths[0])
            if not raw_text:
                # Fall back to body HTML from WP REST API if available
                body_html = n.get("body_html", "")
                if body_html:
                    raw_text = BeautifulSoup(body_html, "html.parser").get_text(separator=" ", strip=True)
            if raw_text:
                summary = get_ai_summary(raw_text)

        caption = build_caption(n, summary)

        if pdf_paths:
            if len(pdf_paths) == 1:
                print(f"    Sending 1 attachment to {len(CHAT_IDS)} chat(s)...")
                broadcast_document_file(pdf_paths[0], caption)
            else:
                # Send all attachments as a single media group (album).
                # Telegram supports 2–10 items per group; chunk larger sets.
                total = len(pdf_paths)
                print(f"    Sending {total} attachments as media group to {len(CHAT_IDS)} chat(s)...")
                for chunk_start in range(0, total, 10):
                    chunk = pdf_paths[chunk_start:chunk_start + 10]
                    chunk_caption = caption if chunk_start == 0 else f"📎 <b>Attachments {chunk_start + 1}–{chunk_start + len(chunk)}/{total}</b>"
                    broadcast_media_group_files(chunk, chunk_caption)
                    if chunk_start + 10 < total:
                        time.sleep(1)
            # Append download links for any PDFs that failed to download
            if failed_urls:
                extra = "".join(f'\n📎 <a href="{u}">Download PDF ↗</a>' for u in failed_urls)
                broadcast_text(caption + extra)
        else:
            # No PDF files — send text. If we found URLs but couldn't download them,
            # append them to the caption so the user can tap to open the PDFs manually.
            # (We do NOT use tg_document_url because Telegram's servers cannot fetch
            #  PDFs from the university's server — it requires browser-like headers.)
            if pdf_urls:
                for i, pdf_url in enumerate(pdf_urls):
                    label = f"Download PDF {i + 1}" if len(pdf_urls) > 1 else "Download PDF"
                    caption += f'\n📎 <a href="{pdf_url}">{label} ↗</a>'
            print(f"    Sending text message to {len(CHAT_IDS)} chat(s)...")
            broadcast_text(caption)
    finally:
        for pdf_path in pdf_paths:
            Path(pdf_path).unlink(missing_ok=True)

# ─────────────────────────────────────────────────────────────
# DAILY HEARTBEAT
# ─────────────────────────────────────────────────────────────
HEARTBEAT_INTERVAL_HOURS = 20   # send approximately once per day (20-hour minimum interval to handle scheduling variations)

def maybe_send_heartbeat(seen: dict):
    """Send a daily 'bot is alive' message to admin.

    Fires on the first run after HEARTBEAT_INTERVAL_HOURS have elapsed since
    the last heartbeat (or on the very first run ever).  This approach is
    immune to GitHub Actions scheduler gaps that could cause a fixed time-
    window check to be skipped indefinitely.
    """
    now = datetime.now(timezone.utc)
    hb  = load_json(HEARTBEAT_FILE)

    last_sent = hb.get("last_sent")
    if last_sent:
        try:
            last_dt = datetime.fromisoformat(last_sent)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (now - last_dt).total_seconds() < HEARTBEAT_INTERVAL_HOURS * 3600:
                return   # too soon — not yet 20 hours since last heartbeat
        except Exception:
            pass  # unparseable timestamp → treat as "never sent"

    total = len(seen)
    msg = (
        f"💚 <b>Bot is running fine</b>\n\n"
        f"🕗 Daily check — {now.strftime('%d %b %Y %H:%M')} UTC\n"
        f"📊 Notifications tracked so far: <b>{total}</b>\n"
        f"⏱ Check interval: every 5 minutes\n\n"
        f"🏛 <i>Pondicherry University Notification Bot</i>"
    )
    if ADMIN_CHAT_ID:
        tg_text(ADMIN_CHAT_ID, msg)

    hb["last_sent"] = now.isoformat()
    save_json(HEARTBEAT_FILE, hb)

# ─────────────────────────────────────────────────────────────
# SEEN.JSON PRUNING
# ─────────────────────────────────────────────────────────────
PRUNE_DAYS = 180

def prune_seen(seen: dict) -> dict:
    """Remove notified entries older than PRUNE_DAYS to keep seen.json compact.
    'seeded' entries (initial baseline) are never pruned.
    """
    cutoff  = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    pruned  = {}
    removed = 0
    for nid, meta in seen.items():
        notified = meta.get("notified", "")
        if notified == "seeded":
            pruned[nid] = meta
            continue
        try:
            ts = datetime.fromisoformat(notified)
            # Treat naive timestamps (stored before timezone support) as UTC
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                pruned[nid] = meta
            else:
                removed += 1
        except Exception:
            pruned[nid] = meta  # keep entries we can't parse
    if removed:
        print(f"  🗑  Pruned {removed} old entries from seen.json (>{PRUNE_DAYS} days)")
    return pruned

# ─────────────────────────────────────────────────────────────
# RESEND HELPER
# ─────────────────────────────────────────────────────────────
def _resend_last(n: int, seen: dict, recent_notifications: list[dict]):
    """Re-deliver the last *n* notified entries.

    Strategy:
    1. Sort seen entries that have a real ISO timestamp (i.e. not "seeded")
       in descending order and take the first *n*.
    2. Try to match each against the already-fetched *recent_notifications*
       list so we reuse the full notification object (including pdf_urls).
    3. If not found there, build a minimal stub from seen metadata and
       re-fetch the PDF from the notification's link URL.
    """
    print(f"\n  🔁 RESEND mode — re-delivering last {n} notification(s).")

    # Collect entries with a parseable ISO timestamp
    timed: list[tuple[datetime, str, dict]] = []
    for nid, meta in seen.items():
        ts_str = meta.get("notified", "")
        if ts_str in ("", "seeded"):
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            timed.append((ts, nid, meta))
        except Exception:
            pass

    if not timed:
        print("  No previously-notified entries found in seen.json — nothing to resend.")
        return

    timed.sort(key=lambda x: x[0], reverse=True)
    targets = timed[:n]

    # Build a lookup by id from the freshly-fetched notifications
    fresh_by_id = {item["id"]: item for item in recent_notifications}

    for ts, nid, meta in targets:
        title = meta.get("title", nid)
        print(f"\n  🔁 Resending: {title[:70]}")

        notif = fresh_by_id.get(nid)
        if notif is None:
            # Build a stub so deliver() can at least send a text message
            notif = {
                "id":        nid,
                "title":     title,
                "link":      nid if nid.startswith("http") else "",
                "category":  meta.get("category", "General"),
                "issued_by": "",
                "date":      meta.get("date", ""),
            }

        try:
            deliver(notif)
        except Exception as e:
            print(f"    ERROR resending: {e}")
            alert_admin(f"Error resending notification:\n<b>{title}</b>\n\n{e}")

        time.sleep(3)

    print(f"\n  ✅ Resend complete ({len(targets)} notification(s)).")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  PU Notification Bot v2  —  {ts}")
    print(f"  Recipients: {len(CHAT_IDS)} chat(s)")
    print(f"{'='*55}")

    if not CHAT_IDS:
        print("ERROR: No TELEGRAM_CHAT_IDS configured.")
        return

    seen         = load_json(SEEN_FILE)
    # Determine first-run status BEFORE pruning: if the file already had entries
    # but all of them expired, we should NOT re-seed and silence new notifications.
    is_first_run = len(seen) == 0
    seen         = prune_seen(seen)

    notifications = []
    try:
        notifications = fetch_all_notifications(seen_ids=set(seen.keys()))
    except Exception as e:
        err_msg = f"Failed to fetch notifications: {e}"
        print(f"  ERROR: {err_msg}")
        alert_admin(err_msg)
        return

    if is_first_run:
        if len(notifications) == 0:
            err_msg = (
                "First run completed but scraped 0 notifications.\n"
                "The WP REST API and HTML scraper both returned empty results.\n"
                "Check the site URL and scraper selectors."
            )
            print(f"\n  ❌ {err_msg}")
            alert_admin(err_msg)
            return
        print("  ⚡ First run — seeding seen.json without sending alerts.")

    new_count = 0
    errors    = 0

    for n in notifications:
        nid = n["id"]
        if nid in seen:
            continue

        if is_first_run:
            seen[nid] = {
                "title":    n["title"],
                "date":     n.get("date", ""),
                "category": n.get("category", ""),
                "notified": "seeded",
            }
            continue

        print(f"\n  🆕 {n['title'][:70]}")
        print(f"     {n.get('category','')}  |  {n.get('date','')}")

        # Mark as seen BEFORE delivering — prevents re-sends if job times out mid-run
        seen[nid] = {
            "title":    n["title"],
            "date":     n.get("date", ""),
            "category": n.get("category", ""),
            "notified": datetime.now(timezone.utc).isoformat(),
        }
        save_json(SEEN_FILE, seen)   # persist immediately

        try:
            deliver(n)
            new_count += 1
        except Exception as e:
            print(f"    ERROR delivering: {e}")
            errors += 1
            alert_admin(f"Error delivering notification:\n<b>{n['title']}</b>\n\n{e}")

        time.sleep(3)

    save_json(SEEN_FILE, seen)

    if is_first_run:
        print(f"\n  ✅ Seeded {len(seen)} existing notifications. Bot is now active!")
        broadcast_text(
            f"✅ <b>PU Notification Bot v2 is now active!</b>\n\n"
            f"I've catalogued <b>{len(seen)}</b> existing notifications.\n"
            f"You'll get alerts for every <b>new</b> one from now on — with PDF! 🎉\n\n"
            f"👥 Broadcasting to <b>{len(CHAT_IDS)}</b> chat(s)\n"
            f"⏱ Checking every <b>5 minutes</b>\n\n"
            f"🏛 <i>Pondicherry University</i>"
        )
    else:
        print(f"\n  ✅ Done. {new_count} new | {errors} errors.")
        maybe_send_heartbeat(seen)

    # ── Resend last N notifications ───────────────────────────
    if RESEND_LAST > 0 and not is_first_run:
        _resend_last(RESEND_LAST, seen, notifications)


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────
# TESTS  (run with: python scraper.py --test)
# ─────────────────────────────────────────────────────────────
def _run_tests():
    """Minimal self-contained tests for choose_primary_pdf_url()."""
    import sys

    passed = 0
    failed = 0

    def _check(name: str, got, expected):
        nonlocal passed, failed
        if got == expected:
            print(f"  ✅  {name}")
            passed += 1
        else:
            print(f"  ❌  {name}")
            print(f"      expected: {expected}")
            print(f"      got:      {got}")
            failed += 1

    # 1. Returns None for empty input
    _check("empty list → None", choose_primary_pdf_url([]), None)

    # 2. Single URL is returned as-is
    _check(
        "single URL returned as-is",
        choose_primary_pdf_url(["https://example.com/doc.pdf"]),
        "https://example.com/doc.pdf",
    )

    # 3. Circular beats annexure
    _check(
        "circular beats annexure",
        choose_primary_pdf_url([
            "https://www.pondiuni.edu.in/wp-content/uploads/2025/04/Annexure-form.pdf",
            "https://www.pondiuni.edu.in/wp-content/uploads/2025/04/Circular-Hostel.pdf",
        ]),
        "https://www.pondiuni.edu.in/wp-content/uploads/2025/04/Circular-Hostel.pdf",
    )

    # 4. Direct .pdf beats non-.pdf URL
    _check(
        "direct .pdf beats viewer URL",
        choose_primary_pdf_url([
            "https://www.pondiuni.edu.in/viewer?file=notice.pdf",
            "https://www.pondiuni.edu.in/wp-content/uploads/notice.pdf",
        ]),
        "https://www.pondiuni.edu.in/wp-content/uploads/notice.pdf",
    )

    # 5. University domain preferred over external
    _check(
        "university domain preferred",
        choose_primary_pdf_url([
            "https://external-cdn.example.com/doc.pdf",
            "https://www.pondiuni.edu.in/wp-content/uploads/doc.pdf",
        ]),
        "https://www.pondiuni.edu.in/wp-content/uploads/doc.pdf",
    )

    # 6. Title-word bonus helps pick correct PDF
    _check(
        "title word bonus picks matching PDF",
        choose_primary_pdf_url(
            [
                "https://www.pondiuni.edu.in/wp-content/uploads/2025/04/Prospectus-2025.pdf",
                "https://www.pondiuni.edu.in/wp-content/uploads/2025/04/Notice-Hostel-Vacating.pdf",
            ],
            title="Hostel Residents Vacating Notice",
        ),
        "https://www.pondiuni.edu.in/wp-content/uploads/2025/04/Notice-Hostel-Vacating.pdf",
    )

    # 7. First URL wins on equal score
    urls_equal = [
        "https://www.pondiuni.edu.in/wp-content/uploads/a.pdf",
        "https://www.pondiuni.edu.in/wp-content/uploads/b.pdf",
    ]
    _check("tie → first URL wins", choose_primary_pdf_url(urls_equal), urls_equal[0])

    print(f"\n  {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if len(__import__("sys").argv) > 1 and __import__("sys").argv[1] == "--test":
    _run_tests()
