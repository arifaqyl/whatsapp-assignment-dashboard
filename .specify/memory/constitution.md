# Student Bot Constitution

## Core Principles

### I. Grounded Before Generative
Every user-visible academic output MUST be traceable to stored VLE rows, WhatsApp rows, manual operator actions, or explicit configuration. AI may classify, summarize, or rank uncertainty, but it MUST not invent deadlines, dates, or evidence. If confidence is low, the system MUST fall back to deterministic behavior and surface the ambiguity.

### II. Reliability Beats Cleverness
Features that touch intake, parsing, promotion, deadline mutation, or `/summary` MUST prefer deterministic rules, idempotent writes, and recoverable retries over higher model usage or hidden heuristics. New automation MUST include failure handling that preserves raw inputs first and derives higher-level state second.

### III. Test The Risk, Not The Syntax
Any change to parsing, deduplication, reschedule handling, promotion logic, persistence, or dashboard rendering MUST add or update focused tests. Priority goes to regression fixtures for real message formats, deterministic pure-function tests, and integration coverage around save-and-promote flows.

### IV. Observable Operations
Long-running flows MUST leave enough evidence to debug without guesswork. Intake, promotion, repair, scraper, digest, and operator actions MUST log structured reasons for skips, replacements, failures, and retries. Any admin surface MUST expose queue state, last successful runs, and unresolved items.

### V. Small Safe Increments
Work ships as independently useful slices. Each feature spec MUST identify an MVP slice that can be tested and demonstrated on its own. Avoid broad rewrites when a narrower path can improve reliability, operator visibility, or deployment clarity.

## Project Constraints

- Primary stack remains Python on the existing repository layout.
- SQLite remains acceptable for local and current server operation unless a feature explicitly requires a migration path.
- Secrets stay local in `config.py` or deployment environment, never in committed docs, fixtures, or generated artifacts.
- Browser/session artifacts, agent-local artifacts, and databases stay ignored by git.
- Public-safe repo behavior matters: features should keep the project explainable and demoable without exposing private school-specific or credential-specific data.

## Development Workflow

- Start meaningful features with a spec in `specs/`.
- Before implementation, define user stories, success criteria, and explicit scope boundaries.
- For risky changes, create or update regression fixtures before editing behavior.
- Before merge, verify at least the narrowest relevant test slice plus one end-to-end sanity path.
- Stable workflow or architecture changes MUST be reflected in the vault project note.

## Governance

This constitution overrides ad hoc feature convenience. Plans and tasks that violate these principles need an explicit justification in the complexity tracking section. Any change that weakens traceability, deterministic fallbacks, or operator visibility must be treated as a regression unless proven otherwise.

**Version**: 1.0.0 | **Ratified**: 2026-06-14 | **Last Amended**: 2026-06-14
