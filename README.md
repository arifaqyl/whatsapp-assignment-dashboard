# student-bot 🤖

Automated academic assistant for UniKL students. Runs 24/7 on a cloud server — scrapes the university VLE (Moodle) and monitors WhatsApp group chats, delivering unified deadline alerts to a personal Telegram bot.

## The Problem

University information is scattered across:
- **VLE (Moodle)** — formal assignments, but sometimes posted late or buried in PDFs
- **WhatsApp groups** — last-minute changes, reminders, rescheduling that never make it to VLE
- **Course Learning Plan PDFs** — semester-long assessment schedule, but lecturers don't always follow them exactly

Manually checking 6 course pages + 14 WhatsApp groups daily is unsustainable.

## The Solution

A single Telegram bot that aggregates everything:

```
VLE Moodle ──────┐
                 ├──► Telegram Bot ◄── /tasks, /list, /check
WhatsApp Groups ─┘
```

## Features

- **Deep VLE scraping** — reads every course page, opens resource PDFs, extracts assignments even when they're not formal Moodle submission activities
- **CLP PDF parsing** — reads Course Learning Plan documents and extracts all assessments with percentage weights
- **WhatsApp monitoring** — captures messages from class groups mentioning deadlines, cancellations, quiz announcements
- **Unified Telegram interface** — one place to see and manage everything
- **Task management** — mark tasks done, delete, undo, filter by status

## Tech Stack

| Layer | Tech |
|-------|------|
| VLE scraping | Python, Playwright (headless Chromium), requests |
| PDF reading | pdftotext (poppler), pdfminer.six, python-docx |
| WhatsApp bridge | [WAHA](https://github.com/devlikeapro/waha) (Docker) |
| Webhook receiver | Flask |
| Telegram bot | Python requests (long-polling) |
| Storage | SQLite (deadlines.db, messages.db) |
| Hosting | DigitalOcean Droplet — Ubuntu 24.04 |
| Process manager | PM2 |

## Telegram Commands

```
/tasks          — pending deadline tasks
/check 5        — mark task 5 done
/list           — pending WhatsApp messages  
/done 3         — mark WA message handled
/scrape         — re-scan VLE now
/today          — today's WA messages
/digest         — send morning summary now
/stats          — show counts
/help           — full command list
```

## Architecture

```
┌─────────────────────────────────────────────┐
│              DigitalOcean Droplet            │
│                                             │
│  ┌──────────────┐    ┌──────────────────┐  │
│  │ vle_scraper  │    │ webhook_receiver  │  │
│  │ (Playwright) │    │ (Flask :8085)     │  │
│  └──────┬───────┘    └────────┬─────────┘  │
│         │                     │             │
│         ▼                     ▼             │
│  ┌─────────────┐    ┌──────────────────┐   │
│  │ deadlines.db│    │   messages.db    │   │
│  └──────┬──────┘    └────────┬─────────┘  │
│         └──────────┬──────────┘            │
│                    ▼                        │
│             ┌────────────┐                 │
│             │   bot.py   │◄── Telegram     │
│             │ (PM2)      │──► Telegram     │
│             └────────────┘                 │
│                                             │
│  ┌──────────────────────┐                  │
│  │  WAHA Docker :2785   │◄── WhatsApp      │
│  │  (WhatsApp bridge)   │──► :8085         │
│  └──────────────────────┘                  │
└─────────────────────────────────────────────┘
```

## Setup

1. Clone repo
2. Copy `config.example.py` → `config.py` and fill in Telegram credentials
3. Install dependencies: `pip install -r requirements.txt`
4. Install Playwright browsers: `playwright install chromium`
5. Run `get_session.py` to capture VLE browser session → `storageState.json`
6. Start WAHA Docker container for WhatsApp bridge
7. Use PM2 to run `bot.py` and `webhook_receiver.py`
