# Smart Tracker Master Plan

## Product Core

Smart Tracker is a unified learning task lifecycle product for student, parent, and teacher.  
It transforms unstructured assignments into executable tasks, tracks completion, and escalates risks.

## North Star Scenario

Unstructured input -> structured task -> student execution -> parent/teacher visibility -> timely intervention.

## Stack Guardrails

- Keep existing stack: Python, FastAPI, Jinja2, SQLAlchemy, Alembic, SQLite/PostgreSQL, aiogram, APScheduler.
- No MAX bot.
- AI core is local Ollama.
- Cloud AI APIs are optional edge services only for OCR and STT.

## Mandatory Modules

1. Auth + role access + Telegram 2FA
2. Task lifecycle and progress timeline
3. Reminder and escalation policy
4. AI parsing with teacher confirmation
5. Role-first web dashboards
6. Telegram delivery and status nudges
7. Demo mode with acceptance checks

## Data Backbone

- users
- tasks
- progress
- reminders
- AI metadata inside tasks for traceability:
  - source_type
  - parse_confidence
  - parse_raw_input
  - ai_suggestions_json

## Quality Gates

- Parsing flow returns deterministic structured fields.
- Teacher confirms AI result before publishing.
- Reminder policy is idempotent and has stop conditions.
- Every role has actionable first-screen block.
- Demo acceptance endpoint returns measurable proof.

## Competition Scope (Must)

- Full web + Telegram role loop
- Ollama-powered parsing + fallback behavior
- Overdue detection + escalation to parent
- Strong visual demo path

## Deferred Scope (Out of First Version)

- Deep enterprise policy systems
- Multi-tenant school administration layers
- Heavy long-horizon predictive analytics
