# 🔔 Pondicherry University Notification Bot v2

Automatically monitors [Pondicherry University's notification page](https://www.pondiuni.edu.in/all-notifications/) and sends Telegram alerts with PDF attachments for every new notification — designed to run as a continuous Python process on Azure App Service Linux.

---

## ✦ Features

- **7 categories monitored** — Circulars, News & Announcements, Ph.D Notifications, Events, Admission, Careers, Tenders
- **DDE notifications** — Announcements, Exam Notifications, and Exam Results from the Directorate of Distance Education (`dde.pondiuni.edu.in`)
- **CUET-PG / NTA notifications** — Notices, Instructions, Date Sheet, Admit Cards, Answer Keys, and Results from [https://exams.nta.nic.in/cuet-pg/](https://exams.nta.nic.in/cuet-pg/)
- **PDF delivery** — attaches the notification's PDF directly to the Telegram message
- **AI summary** — optional 2–3 sentence summary of each notification powered by Google Gemini Flash (set `GEMINI_API_KEY` secret to enable)
- **Multi-recipient** — broadcast to multiple user chats
- **Admin-only error alerts** — failures go to the first chat ID only
- **Daily heartbeat** — fires approximately once per day (first run after 20+ hours since the last heartbeat) so you know the bot is alive
- **SQLite persistence** — seen notifications, monitored sites, bot state, and stats are stored in `uninotif.db`
- **Auto-pruning** — entries older than 180 days are removed from SQLite automatically to keep the database compact; seeded (baseline) entries are never pruned
- **Telegram admin commands** — `/status`, `/sites`, `/addsite`, `/removesite`, `/latest`
- **Persistent scrape history** — every scrape cycle writes success/failure counts and run stats to SQLite
- **Azure-ready runtime** — continuous process loop, no external scheduler required

---

## 📁 File Structure

```
├── scraper.py                        # Main bot script
├── uninotif.db                       # SQLite database (seen IDs, monitored sites, stats, history)
├── requirements.txt                  # Python dependencies
└── .github/workflows/notify.yml      # Legacy GitHub Actions scheduler (manually delete after Azure deployment is confirmed healthy)
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
- Add each Telegram user chat ID you want to notify (comma-separated in `TELEGRAM_CHAT_IDS`)

### 4. Configure environment variables

Set these environment variables in Azure App Service (**Configuration → Application settings**):

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_IDS` | Comma-separated user chat IDs, e.g. `123456789,987654321` |
| `GEMINI_API_KEY` | *(Optional)* Google Gemini API key — enables AI summaries |
| `SQLITE_DB_PATH` | *(Optional)* SQLite DB file path (default: `uninotif.db`) |
| `RUN_24X7` | *(Optional)* `true` (default) for continuous 24/7 scraping |

> ⚠️ The **first** chat ID is treated as the admin — it receives error alerts and the daily heartbeat. Additional IDs receive notifications only.

> 💡 **AI summaries** are silently skipped when `GEMINI_API_KEY` is not set, so the bot works without it. To disable summaries while keeping the key, set `ENABLE_AI_SUMMARY=false`.

### 5. Set Azure startup command

Set startup command to:

```bash
python scraper.py
```

### 6. Start the app

Start/restart the App Service. The bot process will begin running continuously.

On first run the bot will:
- Scrape all current notifications and seed them into SQLite (without sending alerts)
- Send you an activation message confirming how many notifications were catalogued
- From that point on, only **new** notifications trigger alerts

---

## 🕐 Schedule

The bot runs in an internal loop:

- checks every **5 minutes**
- uses **Asia/Kolkata (IST)** timezone
- performs scraping every **5 minutes, 24/7 by default**
- optional legacy window mode via `RUN_24X7=false` (09:00–21:00 IST)

---

## 🤖 Admin Commands

Admin commands are accepted from the first chat ID in `TELEGRAM_CHAT_IDS`:

- `/status` — service metrics (`last scrape`, `total sites`, `failed sites`, `notifications sent`)
- `/sites` — list monitored sites from SQLite
- `/addsite <url> [category]` — add/enable a custom monitored site
- `/removesite <site_id_or_url>` — disable a monitored site
- `/latest [count]` — show latest sent notifications (default 5, max 10)

---

## 📦 Dependencies

```
requests
beautifulsoup4
lxml
google-genai        # AI summaries (optional feature)
pdfplumber          # PDF text extraction for AI summaries
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
| "catalogued 0 notifications" on first run | Scraper couldn't reach the site | Check App Service logs for HTTP errors; restart after a few minutes |
| Old notifications being re-sent | SQLite DB was reset/lost | Ensure persistent App Service storage and keep `uninotif.db` intact |
| Same PDF attached to every notification | Site nav/footer PDF was being matched | Fixed in v2 — bot now searches only the post content area |
| Bot appears idle at night | `RUN_24X7=false` set | Remove/flip that setting to run 24/7 |

---

## 📝 Notes

- The bot uses the **WordPress REST API** (`/wp-json/wp/v2/university_news`) as its primary source, falling back to HTML scraping if the API is unavailable.
- PDFs over 49 MB are skipped (Telegram's file size limit is 50 MB).
- The bot sends at most one Telegram message per notification per run; if PDF download fails, it falls back to sending a text message with the notification link.
- Seen entries older than 180 days are pruned automatically on each run; seeded baseline entries are never pruned.

---

*Built for personal use. Not affiliated with Pondicherry University.*
