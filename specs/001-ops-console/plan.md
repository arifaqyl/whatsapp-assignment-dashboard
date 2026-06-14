# Implementation Plan: Operations Console And Evidence Queue

**Branch**: `001-ops-console` | **Date**: 2026-06-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-ops-console/spec.md`

## Summary

Add a private operator console on top of the current Student Bot stack. The feature introduces a durable evidence queue for ambiguous or failed extraction events, a lightweight web UI for review and resolution, and audit/health tables that make the system observable without reading raw SQLite files or log streams.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: Flask, Jinja2, existing bot/webhook modules, SQLite stdlib access layer, pytest

**Storage**: Existing SQLite databases plus new queue/audit/health tables in the current project-local database layer

**Testing**: pytest with unit, integration, and regression fixtures

**Target Platform**: Linux server on DigitalOcean and local Windows development

**Project Type**: Python automation service with lightweight web admin surface

**Performance Goals**: Console renders first page in under 500 ms locally and under 1 s on droplet-scale data sets under 500 unresolved items

**Constraints**: Must remain single-operator, private, low-dependency, deterministic-first, and compatible with current process layout

**Scale/Scope**: 6 tracked courses, 24/7 intake loop, hundreds to low-thousands of raw message rows, dozens of actionable queue items at a time

## Constitution Check

- **Grounded Before Generative**: Pass. Queue items are backed by stored source rows and reason codes.
- **Reliability Beats Cleverness**: Pass if queue writes happen before any AI-assisted resolution and all operator actions are idempotent.
- **Test The Risk, Not The Syntax**: Pass only if parser ambiguity, approval idempotency, and health rendering get tests.
- **Observable Operations**: Pass. This feature directly adds audit and health visibility.
- **Small Safe Increments**: Pass. MVP slice is read-only queue/health console before mutation actions.

## Project Structure

### Documentation (this feature)

```text
specs/001-ops-console/
├── plan.md
├── spec.md
└── tasks.md
```

### Source Code (repository root)

```text
bot.py
db.py
deadline_utils.py
digest.py
webhook_receiver.py
whatsapp_deadlines.py
vle_scraper.py
ops_console/
├── __init__.py
├── app.py
├── routes.py
├── services.py
├── templates/
│   ├── layout.html
│   ├── queue.html
│   └── health.html
└── static/
tests/
├── test_ops_console.py
├── test_evidence_queue.py
└── fixtures/
```

**Structure Decision**: Keep the current single-project layout. Add a small `ops_console/` package rather than splitting frontend/backend. Reuse existing persistence and parsing modules where possible.

## Data Design

- Extend persistence with `evidence_queue`, `operator_actions`, and `system_health` tables.
- Normalize reason codes so queue filters and metrics stay stable.
- Keep raw evidence in existing source tables; queue rows store references and compact previews instead of full copies.
- Add idempotency-safe state transitions for queue item resolution.

## Integration Notes

- `webhook_receiver.py` and `whatsapp_deadlines.py` should enqueue ambiguous or failed promotions.
- `vle_scraper.py` should enqueue ambiguous extraction failures and update health snapshots.
- Existing Telegram flows stay unchanged for MVP except optional links or hints pointing to the console.
- The console should run as a small Flask service that can share the same path/config helpers as the rest of the repo.

## Risks

- Mixing queue state across `deadlines.db` and `messages.db` can create drift if not centralized.
- Audit/health updates can become noisy if every low-level event is logged without reason-code discipline.
- UI scope can bloat; keep the first cut server-rendered and operator-only.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Added web console package | Needed to expose operator workflow clearly | Telegram-only controls hide context and are weak for queue triage |
