# 🔔 Pondicherry University Notification Bot v2

Automatically monitors [Pondicherry University's notification page](https://www.pondiuni.edu.in/all-notifications/) and sends Telegram alerts with PDF attachments for every new notification — powered by GitHub Actions, zero hosting cost.

---

## ✦ Features

- **7 categories monitored** — Circulars, News & Announcements, Ph.D Notifications, Events, Admission, Careers, Tenders
- **PDF delivery** — attaches the notification's PDF directly to the Telegram message
- **Multi-recipient** — broadcast to personal chat + group chats simultaneously
- **Admin-only error alerts** — failures go to the first chat ID only
- **Daily heartbeat** — 8:00 AM IST status message so you know the bot is alive
- **Smart deduplication** — `seen.json` committed to repo after every run; re-sends are prevented even if the job crashes mid-run
- **Auto-pruning** — entries older than 180 days are removed from `seen.json` automatically to keep the file compact; seeded (baseline) entries are never pruned
- **Runs free** — GitHub Actions scheduled workflow, no server needed

---

## 📁 File Structure

```
├── scraper.py                        # Main bot script
├── seen.json                         # Tracks notified IDs (auto-updated by bot)
├── heartbeat.json                    # Tracks daily heartbeat (auto-updated by bot)
├── requirements.txt                  # Python dependencies
└── .github/
    └── workflows/
        └── notify.yml                # GitHub Actions workflow (runs every 15 min)
```

---

## ⚙️ Setup

### 1. Fork / clone this repo

Create a **private** repository on GitHub and push these files.

### 2. Create a Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456789:ABCdef...`)

### 3. Get your Chat ID(s)

- **Personal chat**: Message [@userinfobot](https://t.me/userinfobot) — it replies with your chat ID
- **Group chat**: Add your bot to the group, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id":-100xxxxxxxxx}`

### 4. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_IDS` | Comma-separated chat IDs, e.g. `123456789,-1001234567890` |

> ⚠️ The **first** chat ID is treated as the admin — it receives error alerts and the daily heartbeat. Additional IDs receive notifications only.

### 5. Enable Actions & set permissions

1. Go to **Settings → Actions → General**
2. Under *Workflow permissions*, select **Read and write permissions**
3. Click Save

### 6. Trigger first run

Go to **Actions → 🔔 PU Notification Bot v2 → Run workflow**.

On first run the bot will:
- Scrape all current notifications and save them to `seen.json` (without sending alerts)
- Send you an activation message confirming how many notifications were catalogued
- From that point on, only **new** notifications trigger alerts

---

## 🕐 Schedule

The workflow runs **every 5 minutes** (`*/5 * * * *`) — the minimum interval supported by GitHub Actions.

To change the interval, edit `notify.yml`:

```yaml
- cron: '*/5 * * * *'          # every 5 min (GitHub Actions minimum)
# - cron: '3,18,33,48 * * * *' # every 15 min (lower resource usage)
```

> **Note:** GitHub Actions free tier does not guarantee exact timing. Scheduled runs can be delayed 5–30 minutes during peak hours.

---

## 🔄 Reseed / Reset

If you want to wipe `seen.json` and re-catalogue from scratch (without sending alerts):

1. Go to **Actions → 🔔 PU Notification Bot v2 → Run workflow**
2. Check **"Clear seen.json and re-seed"**
3. Click **Run workflow**

---

## 📦 Dependencies

```
requests
beautifulsoup4
lxml
```

Install locally with:
```bash
pip install -r requirements.txt
```

---

## 🧪 Run Locally

```bash
export TELEGRAM_BOT_TOKEN="your_token"
export TELEGRAM_CHAT_IDS="your_chat_id"
python scraper.py
```

---

## 🛠 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "catalogued 0 notifications" on first run | Scraper couldn't reach the site | Check Actions log for HTTP error; re-run after a few minutes |
| Old notifications being re-sent | `seen.json` push failed (concurrent runs) | Check Actions log for push errors; the bot retries 4 times automatically |
| Same PDF attached to every notification | Site nav/footer PDF was being matched | Fixed in v2 — bot now searches only the post content area |
| Workflow runs late / not at all | GitHub Actions free-tier queue delay | Normal behaviour; consider reducing to 1 repo if running v1 + v2 in parallel |
| Bot stopped sending (no errors) | GitHub disables scheduled workflows after **60 days of repo inactivity** | Push any commit to the repo to re-enable, or manually trigger a run |

---

## 📝 Notes

- The bot uses the **WordPress REST API** (`/wp-json/wp/v2/university_news`) as its primary source, falling back to HTML scraping if the API is unavailable.
- PDFs over 49 MB are skipped (Telegram's file size limit is 50 MB).
- The bot sends at most one Telegram message per notification per run; if PDF download fails, it falls back to sending a text message with the notification link.
- `seen.json` entries older than 180 days are pruned automatically on each run; entries from the initial seeding baseline are never pruned.

---

*Built for personal use. Not affiliated with Pondicherry University.*
