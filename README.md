# whatsapp-assignment-dashboard

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![WAHA](https://img.shields.io/badge/WAHA-WhatsApp%20Bridge-25D366.svg)

Academic tracker for universities that run a Moodle-style VLE and WhatsApp groups.
It pulls deadlines from course pages and PDFs, watches course WhatsApp groups through WAHA, and turns the noise into one compact Telegram dashboard.

Formerly `student-bot`.

## What It Does

Academic info usually lives across:

- **VLE / Moodle** for formal assignments and announcements
- **WhatsApp groups** for last-minute reminders, cancellations, and schedule changes
- **Course plans / PDFs** for semester schedules that drift from reality

## Why It Exists

Manually checking multiple course pages and group chats every day is noisy and easy to miss.

This bot keeps the task list in one place:

```text
VLE Moodle ----------.
                     +--> Telegram Bot <-- /summary, /agenda, /check
WhatsApp Groups -----'
```

## Highlights

- Deep VLE scraping with Playwright
- PDF/resource-name scanning before downloads
- page-text extraction for due-date blocks like assignment/project/exam sections
- Course-plan PDF parsing
- WhatsApp monitoring through WAHA
- Unified Telegram interface for alerts, summaries, and task management
- Urgency grouping for today, this week, and later
- Deduping and cancellation handling for WhatsApp-driven updates
- SQLite-backed storage for deadlines and message state
- Read-only ops console for unresolved evidence and pipeline health

## Demo Preview

![Dashboard preview](docs/demo-dashboard.svg)

Illustrative preview only. The live bot renders a `/summary` dashboard that merges VLE and WhatsApp into one view.

## Tech Stack

| Layer | Tech |
|-------|------|
| VLE scraping | Python, Playwright, requests |
| PDF reading | pdftotext (poppler), pdfminer.six, python-docx |
| WhatsApp bridge | [WAHA](https://github.com/devlikeapro/waha) |
| Webhook receiver | Flask |
| Telegram bot | Python requests |
| Storage | SQLite |
| Hosting | DigitalOcean Droplet (Ubuntu) |
| Process manager | PM2 |

## Telegram Commands

```text
/summary        main merged dashboard
/agenda         alias for /summary
/tasks          pending deadline tasks
/check 5        mark task 5 done
/list           pending WhatsApp messages
/done 3         mark WhatsApp message handled
/scrape         re-scan VLE now
/today          today's WhatsApp messages
/digest         send morning summary now
/stats          show counts
/vle_status     show current VLE login state
/help           full command list
```

## Architecture

```text
VLE scraper ----> deadlines.db --.
                                 +--> bot.py <--> Telegram
Webhook receiver -> messages.db -+--> ops_console (queue + health)

WAHA Docker <--> WhatsApp <--> webhook receiver
```

## Setup

1. Clone the repo.
2. Copy `config.example.py` to `config.py` and fill in Telegram, VLE, and WAHA settings.
3. Install dependencies with `pip install -r requirements.txt`.
4. Install Playwright browsers with `playwright install chromium`.
5. Run `get_session.py` to capture the VLE browser session into `storageState.json`.
6. Start the WAHA Docker container for the WhatsApp bridge.
7. Use PM2 to run `bot.py`, `webhook_receiver.py`, and the scraper worker.

## Ops Console

Run locally:

```text
python -m ops_console.app
# or
python run_ops_console.py
```

Then open:

```text
http://127.0.0.1:8090/queue
http://127.0.0.1:8090/health
http://127.0.0.1:8090/queue/1
```

Current MVP:

- unresolved evidence queue with approve, retry, and dismiss actions
- queue page now supports status/source/reason filters and pagination
- per-item detail page with direct approve/retry/dismiss controls plus action history
- queue actions now preserve filter/page context, and detail-page actions stay on the item with status feedback
- queue counts now drill straight into filtered views, health links jump directly into queue items, and item pages include a real back-to-queue path
- queue page now exposes status totals for pending/resolved/dismissed/all, and health now surfaces a compact attention panel for non-ok components plus high-volume pending queue reasons
- queue item pages now render richer source metadata: WhatsApp sender/raw payload details, VLE host/path/query hints, and prettified action payload JSON when available
- health now classifies components into `critical` / `warn` / `ok` and surfaces those totals directly in the page header
- WhatsApp-backed queue items now show their saved source message row on the detail page
- VLE-backed queue items now show the stored resource URL/reference on the detail page
- health snapshots for webhook, promotion, scraper, and digest flows
- queue items created when WhatsApp promotion fails or a relevant message produces no deadline
- queue items created for actionable VLE items with missing or weak due-date signals
- WhatsApp retry re-runs deadline promotion from saved message evidence
- VLE retry now re-checks the saved activity/resource link and auto-resolves the item if it can recover a due date
- approve writes a deadline row and resolves the queue item with an audit trail
- VLE page-text extraction now uses broader task-plus-date heuristics so final-project style blocks can be captured without relying on narrow hardcoded patterns
- VLE date parsing now also understands dotted numeric and weekday-led date shapes such as `9.6.2026` and `Monday, June 8, 2026`
- course scrapes now purge old manual `check VLE` placeholders once concrete dated rows for that course are recovered
- `/login` now drives a real headless Microsoft/VLE refresh flow using saved email/password, exposes progress through `/vle_status`, treats `needs_approval` as a phone approval or Microsoft number-match step, and only consumes `/code 123456` when the sign-in flow lands on an OTP screen
- `/vle_status` now also probes the saved `storageState.json` directly and reports whether it is missing, valid, expired, or hitting an unexpected landing URL
- `/vle_status` now also runs a short passive login preview so it can tell you whether the live auth path is currently sitting at Microsoft email, password, approval, OTP, Moodle login, or `/my/`
- Microsoft number-match prompts are now explicitly kept out of the OTP lane, so numeric approval screens no longer flip the live login state to `waiting_code` by mistake
- When Microsoft MFA is active, the login worker now sends a Telegram screenshot of the live number-match or OTP page and includes a direct hint in the caption so the approval/code step is visible again from chat
- VLE auth transitions now also write into the shared `system_health` lane as `vle_login`, so the ops console can surface login trouble alongside scraper/webhook/digest health

PM2/server path:

```text
pm2 start ecosystem.config.js --only ops-console
```

The dedicated runner now binds to loopback by default:

```text
OPS_CONSOLE_HOST=127.0.0.1
OPS_CONSOLE_PORT=8091
```

Optional basic auth for any non-loopback exposure:

```text
OPS_CONSOLE_USERNAME=admin
OPS_CONSOLE_PASSWORD=change-me
```

Health ping:

```text
http://127.0.0.1:8091/api/ping
```

Deployment runbook:

```text
docs/ops-console-deploy.md
```

Dry-run deployment helper:

```text
python deploy_ops_console.py
```

That helper now also syncs shared runtime files like `bot.py` and `deadline_utils.py`, then restarts `student-bot` and `webhook-receiver` so scraper/parser changes do not stay stale in PM2.

Live server state:

- deployed on the DigitalOcean droplet under PM2 as `ops-console`
- verified on loopback with `curl http://127.0.0.1:8091/api/ping`
- queue page also verified live on the droplet
- reverse-proxied through nginx at `http://68.183.181.237/ops-console/`
- public path is protected by HTTP Basic Auth
- nginx now also adds no-store/security headers, restricts methods to `GET/POST/HEAD`, and rate-limits the path
- HTTPS is not on the droplet yet because the current public domain resolves to GitHub Pages, not this server

## Public-Safe Defaults

- `config.py` stays local and is ignored by git.
- `config.example.py` shows the required fields without real secrets.
- Set `VLE_BASE_URL` to your university portal.
- Set `VLE_COURSES` to the course-code map you want scraped, or leave it empty to auto-discover visible course links.
- Set `WHATSAPP_MONITORED_GROUP_ALIASES` to the group-name hints you care about.
- Set `WAHA_API_KEY` and `WAHA_PAIR_NUMBER` locally for your WhatsApp bridge.

## Security Notes

- Keep `config.py` local and never commit it.
- Never commit `storageState.json`, `*.db`, API keys, or VLE credentials.
- Helper scripts should import secrets from `config.py` instead of hardcoding them.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the GitHub issue templates under `.github/`.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the current public plan.
