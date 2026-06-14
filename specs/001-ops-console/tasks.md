# Tasks: Operations Console And Evidence Queue

**Input**: Design documents from `/specs/001-ops-console/`

**Prerequisites**: plan.md, spec.md

**Tests**: Required for queue transitions, ambiguity capture, and console rendering.

**Organization**: Tasks are grouped by user story so each slice can be implemented and demonstrated independently.

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Create `ops_console/` package with `app.py`, `routes.py`, `services.py`, and template directories.
- [ ] T002 Add launch wiring and local run instructions for the console service.
- [ ] T003 [P] Add base test files `tests/test_ops_console.py` and `tests/test_evidence_queue.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T004 Extend `db.py` with schema creation and access helpers for `evidence_queue`, `operator_actions`, and `system_health`.
- [ ] T005 [P] Add reason-code constants and state-transition helpers in `deadline_utils.py` or a new shared module.
- [ ] T006 [P] Add structured health update helpers for scraper, webhook, promotion, and digest flows.
- [ ] T007 Add queue insertion helpers that preserve source references and compact evidence preview payloads.
- [ ] T008 Add regression fixtures for ambiguous WhatsApp and VLE cases in `tests/fixtures/`.

**Checkpoint**: Queue, audit, and health primitives exist and are testable.

---

## Phase 3: User Story 1 - Review unresolved evidence quickly (Priority: P1) 🎯 MVP

**Goal**: Read-only console for unresolved items and health state.

**Independent Test**: Seed queue and health rows, open the console, and confirm queue filters plus health summaries render correctly.

### Tests for User Story 1

- [ ] T009 [P] [US1] Add unit tests for queue list/query helpers in `tests/test_evidence_queue.py`.
- [ ] T010 [P] [US1] Add integration test for queue page rendering in `tests/test_ops_console.py`.

### Implementation for User Story 1

- [ ] T011 [P] [US1] Implement queue query and filter services in `ops_console/services.py`.
- [ ] T012 [P] [US1] Implement health snapshot query services in `ops_console/services.py`.
- [ ] T013 [US1] Add Flask routes for queue and health pages in `ops_console/routes.py`.
- [ ] T014 [US1] Create `ops_console/templates/layout.html`, `queue.html`, and `health.html`.
- [ ] T015 [US1] Wire ambiguous WhatsApp and VLE outcomes to queue insertion helpers.

**Checkpoint**: Operator can inspect unresolved evidence and health without DB access.

---

## Phase 4: User Story 2 - Resolve or dismiss items safely (Priority: P2)

**Goal**: Safe mutation flow for queue item approval, edit-and-approve, dismiss, and retry.

**Independent Test**: Resolve seeded queue items through HTTP actions and verify downstream state plus audit rows.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add tests for idempotent approval and dismissal transitions in `tests/test_evidence_queue.py`.
- [ ] T017 [P] [US2] Add route tests for approve/dismiss/retry flows in `tests/test_ops_console.py`.

### Implementation for User Story 2

- [ ] T018 [P] [US2] Implement operator action persistence in `db.py`.
- [ ] T019 [US2] Implement resolution handlers in `ops_console/services.py`.
- [ ] T020 [US2] Add approve, edit, dismiss, and retry POST routes in `ops_console/routes.py`.
- [ ] T021 [US2] Update templates to surface action forms and resulting state.

**Checkpoint**: Queue items can be resolved safely and auditably.

---

## Phase 5: User Story 3 - Explain system health and recent activity (Priority: P3)

**Goal**: Show recent audit events and freshness of critical pipelines.

**Independent Test**: Trigger health updates and operator actions, then confirm the activity panel reflects them in time order.

### Tests for User Story 3

- [ ] T022 [P] [US3] Add tests for health snapshot updates and activity feed ordering in `tests/test_ops_console.py`.

### Implementation for User Story 3

- [ ] T023 [P] [US3] Implement recent-activity feed queries in `ops_console/services.py`.
- [ ] T024 [US3] Render activity widgets on the console pages.
- [ ] T025 [US3] Update scraper, webhook, promotion, and digest entry points to persist health events.

**Checkpoint**: Console explains current system state and recent changes.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T026 [P] Add docs for running the ops console locally and on the droplet in `README.md`.
- [ ] T027 Verify private/local-only exposure defaults for the console config.
- [ ] T028 [P] Add a vault update describing the new operator workflow once implementation lands.
- [ ] T029 Run targeted pytest slices for queue, parser ambiguity, and console routes.

## Dependencies & Execution Order

- Phase 1 first.
- Phase 2 blocks all story work.
- US1 is the MVP and should ship first.
- US2 depends on queue primitives from Phase 2 and benefits from US1 templates/routes.
- US3 depends on audit and health persistence from Phase 2 and can layer onto US1.

## Implementation Strategy

### MVP First

1. Build queue schema and health primitives.
2. Ship read-only console with queue plus health.
3. Validate it against real local data.

### Incremental Delivery

1. Read-only visibility
2. Safe operator actions
3. Recent activity and fuller health picture

## Notes

- Keep the first UI thin and server-rendered.
- Prefer new helpers over threading queue logic through many call sites by hand.
- Preserve deterministic fallbacks when AI classification is absent or low-confidence.
