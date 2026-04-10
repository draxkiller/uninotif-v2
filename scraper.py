#!/usr/bin/env python3
"""
Pondicherry University — Telegram Notification Bot  v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✦ All 7 categories monitored
✦ Multi-recipient (personal + group)
✦ Error alerts to admin only
✦ Daily heartbeat
✦ 5-minute checks via GitHub Actions
"""

import os, re, json, time, hashlib, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────
# CONFIG  (all values come from GitHub Secrets)
# ─────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
# Comma-separated chat IDs  e.g.  "123456789,-1001234567890"
# First ID = admin (receives error alerts too)
_raw_ids         = os.environ.get("TELEGRAM_CHAT_IDS", os.environ.get("TELEGRAM_CHAT_ID", ""))
CHAT_IDS         = [c.strip() for c in _raw_ids.split(",") if c.strip()]
ADMIN_CHAT_ID    = CHAT_IDS[0] if CHAT_IDS else ""

BASE_URL         = "https://www.pondiuni.edu.in"
NOTIF_URL        = f"{BASE_URL}/all-notifications/"
SEEN_FILE        = "seen.json"
HEARTBEAT_FILE   = "heartbeat.json"

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

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

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
        return results
    results = _scrape_html()
    print(f"  [HTML] {len(results)} notifications via HTML scrape")
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
                all_items.append({
                    "id":        str(item["id"]),
                    "title":     BeautifulSoup(item["title"]["rendered"], "html.parser").get_text(strip=True),
                    "link":      item["link"],
                    "category":  f"{cat_name} {cat_emoji}",
                    "issued_by": "",
                    "date":      _fmt_wp_date(item.get("date", "")),
                })
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

# ─────────────────────────────────────────────────────────────
# PDF EXTRACTION + DOWNLOAD
# ─────────────────────────────────────────────────────────────
def get_pdf_url(detail_url: str) -> str | None:
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

        # 1. Direct <a href="...pdf"> in content
        for a in content.find_all("a", href=True):
            href = a["href"]
            if re.search(r"\.pdf(\?|$)", href, re.I):
                return _abs(href)

        # 2. <embed>, <iframe>, <object> in content
        for tag in content.find_all(["embed", "iframe", "object"]):
            src = tag.get("src") or tag.get("data") or ""
            if re.search(r"\.pdf", src, re.I):
                return _abs(src)

        # 3. JS/text patterns — only inside content's HTML
        content_html = str(content)
        for pat in [
            r'ViewerJS/#(?:https?:)?([^\s"\'<]+\.pdf[^\s"\'<]*)',
            r'file=([^\s&"\'<]+\.pdf[^\s&"\'<]*)',
            r'["\']([^"\']*?/(?:uploads|files|documents|notices|notification)[^"\']*?\.pdf)["\']',
        ]:
            m = re.search(pat, content_html, re.I)
            if m:
                c = m.group(1)
                if len(c) > 8:
                    return _abs(c)

    except Exception as e:
        print(f"    PDF extraction error: {e}")
    return None


def download_pdf(pdf_url: str) -> str | None:
    """
    Download a PDF from pdf_url to a temp file.
    Validates using magic bytes (%PDF) instead of Content-Type header,
    because the university server often returns application/octet-stream
    or other non-standard content types for valid PDFs.
    """
    try:
        uid   = hashlib.md5(pdf_url.encode()).hexdigest()[:10]
        local = f"/tmp/pu_{uid}.pdf"
        with requests.get(pdf_url, headers=HEADERS, timeout=60, stream=True) as r:
            if r.status_code != 200:
                print(f"    PDF download HTTP {r.status_code} — skipping")
                return None
            size        = 0
            first_chunk = None
            with open(local, "wb") as f:
                for chunk in r.iter_content(8192):
                    if first_chunk is None:
                        first_chunk = chunk   # capture for magic byte check
                    f.write(chunk)
                    size += len(chunk)
                    if size > 49 * 1024 * 1024:
                        print("    PDF too large (>49 MB) — skipping")
                        Path(local).unlink(missing_ok=True)
                        return None

            # Validate actual PDF magic bytes — don't trust Content-Type
            if not first_chunk or not first_chunk.startswith(b"%PDF"):
                print(f"    Not a valid PDF (bad magic bytes) — skipping")
                Path(local).unlink(missing_ok=True)
                return None

        file_size = Path(local).stat().st_size
        if file_size <= 512:
            print(f"    PDF too small ({file_size} bytes) — skipping")
            return None

        print(f"    PDF downloaded OK ({file_size // 1024} KB)")
        return local

    except Exception as e:
        print(f"    PDF download error: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────────────────────
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
    with open(path, "rb") as f:
        return _tg_post("sendDocument", chat_id,
            data={"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"document": (Path(path).name, f, "application/pdf")},
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


def alert_admin(text: str):
    """Send error/status messages to admin only."""
    if ADMIN_CHAT_ID:
        tg_text(ADMIN_CHAT_ID, f"⚠️ <b>Bot Alert</b>\n\n{text}")

# ─────────────────────────────────────────────────────────────
# MESSAGE FORMATTING
# ─────────────────────────────────────────────────────────────
def build_caption(n: dict) -> str:
    return (
        f"🔔 <b>NEW NOTIFICATION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 <b>Pondicherry University</b>\n\n"
        f"📁 <b>Category :</b> {n.get('category', 'General')}\n"
        f"📄 <b>Title    :</b> {n['title']}\n"
        f"🏢 <b>Issued by:</b> {n.get('issued_by') or '—'}\n"
        f"📅 <b>Date     :</b> {n.get('date') or '—'}\n\n"
        f"🔗 <a href=\"{n['link']}\">Open on Website ↗</a>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

# ─────────────────────────────────────────────────────────────
# DELIVER ONE NOTIFICATION
# ─────────────────────────────────────────────────────────────
def deliver(n: dict):
    caption  = build_caption(n)
    pdf_url  = get_pdf_url(n["link"])
    pdf_path = None

    if pdf_url:
        print(f"    PDF found → {pdf_url[:80]}")
        pdf_path = download_pdf(pdf_url)
        if not pdf_path:
            print("    PDF download failed — sending text message with link instead")

    if pdf_path:
        print(f"    Sending PDF file to {len(CHAT_IDS)} chat(s)...")
        broadcast_document_file(pdf_path, caption)
    else:
        # No PDF file — send text. If we found a URL but couldn't download it,
        # append it to the caption so the user can tap to open the PDF manually.
        # (We do NOT use tg_document_url because Telegram's servers cannot fetch
        #  PDFs from the university's server — it requires browser-like headers.)
        if pdf_url:
            caption += f'\n📎 <a href="{pdf_url}">Download PDF ↗</a>'
        print(f"    Sending text message to {len(CHAT_IDS)} chat(s)...")
        broadcast_text(caption)

    if pdf_path:
        Path(pdf_path).unlink(missing_ok=True)

# ─────────────────────────────────────────────────────────────
# DAILY HEARTBEAT
# ─────────────────────────────────────────────────────────────
def maybe_send_heartbeat(seen: dict):
    """Send a daily 'bot is alive' message to admin at ~8 AM IST."""
    now = datetime.now(timezone.utc)
    # IST = UTC+5:30 → 8:00 IST = 2:30 UTC
    # We only run every 5 min, so trigger between 2:30–2:35 UTC
    if not (now.hour == 2 and 30 <= now.minute < 35):
        return

    hb    = load_json(HEARTBEAT_FILE)
    today = now.strftime("%Y-%m-%d")
    if hb.get("last_date") == today:
        return   # already sent today

    total = len(seen)
    msg = (
        f"💚 <b>Bot is running fine</b>\n\n"
        f"🕗 Daily check — {now.strftime('%d %b %Y')}\n"
        f"📊 Notifications tracked so far: <b>{total}</b>\n"
        f"⏱ Check interval: every 5 minutes\n\n"
        f"🏛 <i>Pondicherry University Notification Bot</i>"
    )
    if ADMIN_CHAT_ID:
        tg_text(ADMIN_CHAT_ID, msg)

    hb["last_date"] = today
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
    seen         = prune_seen(seen)
    is_first_run = len(seen) == 0

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
        print(f"  ⚡ First run — seeding seen.json without sending alerts.")

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


if __name__ == "__main__":
    main()