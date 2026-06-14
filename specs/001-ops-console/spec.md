# Feature Specification: Operations Console And Evidence Queue

**Feature Branch**: `001-ops-console`

**Created**: 2026-06-14

**Status**: Draft

**Input**: User description: "Turn Student Bot into a deeper flagship system with a grounded admin surface, evidence trail, and safer AI-assisted operations."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Review unresolved evidence quickly (Priority: P1)

As the operator, I want a single console view of unresolved VLE and WhatsApp items so I can see what needs manual review before the digest goes stale.

**Why this priority**: The project is already useful, but operations are still hidden in logs and databases. This is the highest-leverage improvement for reliability and recruiter-facing depth.

**Independent Test**: Seed the system with unresolved promoted items, ambiguous parser outputs, and recent scraper rows; open the console and confirm the queue, reasons, and linked evidence render without Telegram commands or direct database inspection.

**Acceptance Scenarios**:

1. **Given** unresolved parser or promotion items exist, **When** the operator opens the console, **Then** the system shows each item with source, reason, timestamp, and evidence preview.
2. **Given** no unresolved items exist, **When** the operator opens the console, **Then** the queue shows an empty state plus the last successful ingestion timestamps.

---

### User Story 2 - Resolve or dismiss items safely (Priority: P2)

As the operator, I want to approve, edit, dismiss, or retry queue items from the console so I can correct edge cases without raw SQL or ad hoc scripts.

**Why this priority**: Manual repair is currently possible but scattered. A safe operator path raises system depth and reduces maintenance friction.

**Independent Test**: From a seeded queue item, perform approve, dismiss, and retry actions; verify the underlying rows, audit log, and downstream dashboard state update correctly.

**Acceptance Scenarios**:

1. **Given** an ambiguous WhatsApp reminder exists, **When** the operator edits the resolved due date and approves it, **Then** the deadline row is created or updated and the action is audit logged.
2. **Given** a noisy or irrelevant message exists, **When** the operator dismisses it, **Then** it is removed from the unresolved queue without reappearing on the next refresh unless new evidence arrives.

---

### User Story 3 - Explain system health and recent activity (Priority: P3)

As the operator, I want to see scraper health, webhook intake health, digest freshness, and recent changes so I can debug the system faster and explain its architecture clearly.

**Why this priority**: This turns the project from a useful bot into a system with visible operations and measurable behavior.

**Independent Test**: Open the console on a running instance and verify last successful run times, queue counts, and recent audit events reflect current database state and process activity.

**Acceptance Scenarios**:

1. **Given** recent scraper and intake activity exists, **When** the operator opens the health panel, **Then** the panel shows last success time, last failure time, and current backlog counts.
2. **Given** an operator action changes a deadline, **When** the activity feed refreshes, **Then** the feed shows who or what changed the item, when, and why.

---

### Edge Cases

- What happens when the same unresolved evidence item is retried twice?
- How does the system handle operator approval for an item whose backing raw message row was deleted or corrupted?
- What happens when VLE intake is healthy but WhatsApp intake is stale, or vice versa?
- How does the console behave when the queue exceeds 500 unresolved items?
- What happens when two operator actions target the same unresolved item concurrently?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a local web-based operations console that shows unresolved evidence items, recent system activity, and current health state.
- **FR-002**: System MUST store unresolved evidence items in a durable queue instead of relying only on transient logs or Telegram output.
- **FR-003**: System MUST attach each queue item to a raw source row, a reason code, a creation timestamp, and a compact evidence preview.
- **FR-004**: System MUST allow the operator to approve, edit-and-approve, dismiss, or retry a queue item from the console.
- **FR-005**: System MUST audit log every operator action with action type, target item, timestamp, and resulting state.
- **FR-006**: System MUST update downstream deadline state idempotently when an approval action is retried or repeated.
- **FR-007**: System MUST expose last-success and last-failure timestamps for at least VLE scraping, webhook intake, promotion, and digest generation.
- **FR-008**: System MUST preserve deterministic fallback behavior when AI classification is unavailable, slow, or low-confidence.
- **FR-009**: System MUST allow queue creation from both WhatsApp-driven ambiguity and VLE-driven ambiguity or extraction failure classes.
- **FR-010**: System MUST support filtering the queue by source, reason code, and state.
- **FR-011**: System MUST keep the console private to the operator environment and MUST NOT expose secrets or raw credentials in rendered output.
- **FR-012**: System MUST provide a compact recent-activity feed sourced from audit rows and health events.

### Key Entities *(include if feature involves data)*

- **EvidenceQueueItem**: Durable unresolved unit awaiting operator action; includes source type, source row id, reason code, evidence preview, state, timestamps, and optional proposed resolution payload.
- **OperatorAction**: Audit record for approve, edit, dismiss, and retry operations; links actor, target queue item, action payload, and resulting state.
- **SystemHealthSnapshot**: Last-known status for scraper, webhook intake, promotion pipeline, and digest freshness.
- **ResolutionPayload**: Structured operator-provided or model-proposed fields used to create or update downstream deadline state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The operator can identify all unresolved WhatsApp and VLE parsing issues from one console view in under 60 seconds without opening SQLite directly.
- **SC-002**: Approval or dismissal of a queue item updates queue state and audit history in under 2 seconds for 95% of local actions.
- **SC-003**: At least 90% of known parser edge cases currently handled by ad hoc scripts can be represented as queue items and resolved through the console flow.
- **SC-004**: When a queue item is approved twice, the downstream deadline state remains correct and free of duplicate active rows.

## Assumptions

- The first version is single-operator and does not need multi-user auth.
- The console can run on the same host as the existing bot/webhook components.
- Existing SQLite databases remain the source of truth for raw rows and deadline state.
- UI polish is secondary to grounded operations value; server-rendered HTML or a lightweight frontend is acceptable.
