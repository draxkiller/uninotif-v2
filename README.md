# 🔔 Pondicherry University Notification Bot v2

Automatically monitors [Pondicherry University's notification page](https://www.pondiuni.edu.in/all-notifications/) and sends Telegram alerts with PDF attachments for every new notification — designed to run from a scheduled GitHub Actions workflow.

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
- **Smart deduplication** — `seen.json` committed to repo after every run; re-sends are prevented even if the job crashes mid-run
- **Auto-pruning** — entries older than 180 days are removed from `seen.json` automatically to keep the file compact; seeded (baseline) entries are never pruned
- **GitHub Actions scheduler** — runs every 5 minutes and checks only during active IST hours

---

## 📁 File Structure

```
├── scraper.py                        # Main bot script
├── seen.json                         # Tracks notified IDs (auto-updated by bot)
├── heartbeat.json                    # Tracks daily heartbeat (auto-updated by bot)
├── requirements.txt                  # Python dependencies
└── .github/workflows/main_uninotif-v2.yml  # GitHub Actions workflow (scheduled + manual run)
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

Add these as repository secrets in **GitHub → Settings → Secrets and variables → Actions**:

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Your bot token from BotFather |
| `TELEGRAM_CHAT_IDS` | Comma-separated user chat IDs, e.g. `123456789,987654321` |
| `GEMINI_API_KEY` | *(Optional)* Google Gemini API key — enables AI summaries |

> ⚠️ The **first** chat ID is treated as the admin — it receives error alerts and the daily heartbeat. Additional IDs receive notifications only.

> 💡 **AI summaries** are silently skipped when `GEMINI_API_KEY` is not set, so the bot works without it. To disable summaries while keeping the key, set `ENABLE_AI_SUMMARY=false`.

### 5. Run the workflow

The workflow runs automatically every 5 minutes and can also be triggered manually from the Actions tab.

On first successful run the bot will:
- Scrape all current notifications and save them to `seen.json` (without sending alerts)
- Send you an activation message confirming how many notifications were catalogued
- From that point on, only **new** notifications trigger alerts

---

## 🕐 Schedule

The GitHub workflow is scheduled every **5 minutes**. Each run performs one check and:

- uses **Asia/Kolkata (IST)** timezone rules in code
- performs scraping only between **09:00 and 21:00 IST**
- exits without scraping outside that time window

---

## 🔄 Reseed / Reset

If you want to wipe `seen.json` and re-catalogue from scratch (without sending alerts), replace `seen.json` with `{}` and restart the app.

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
| "catalogued 0 notifications" on first run | Scraper couldn't reach the site | Check GitHub Actions logs for HTTP errors; rerun the workflow after a few minutes |
| Old notifications being re-sent | `seen.json` was reset/lost | Ensure persistent app storage and keep `seen.json` intact |
| Same PDF attached to every notification | Site nav/footer PDF was being matched | Fixed in v2 — bot now searches only the post content area |
| Bot appears idle at night | Outside active scraping window | Expected; bot sleeps outside 09:00–21:00 IST |

---

## 📝 Notes

- The bot uses the **WordPress REST API** (`/wp-json/wp/v2/university_news`) as its primary source, falling back to HTML scraping if the API is unavailable.
- PDFs over 49 MB are skipped (Telegram's file size limit is 50 MB).
- The bot sends at most one Telegram message per notification per run; if PDF download fails, it falls back to sending a text message with the notification link.
- `seen.json` entries older than 180 days are pruned automatically on each run; entries from the initial seeding baseline are never pruned.

---

*Built for personal use. Not affiliated with Pondicherry University.*
