# Demo Acceptance Checklist

## Scenario Chain

1. Teacher logs in and opens teacher dashboard.
2. Teacher inputs text assignment and opens AI preview.
3. Teacher publishes task.
4. Student receives Telegram message and sees task in web dashboard.
5. Student sets task to `in_progress` or `done`.
6. Overdue simulation triggers parent reminder.
7. Parent dashboard shows risk and overdue status.
8. `/api/demo/acceptance` returns positive checks.

## Acceptance Criteria

- AI preview returns subject/description/confidence.
- Published task stores AI metadata fields.
- Telegram send path is executed (success or explicit retry record).
- Reminder flow is idempotent (no uncontrolled duplicates).
- Role dashboards show role-specific first-screen priorities.

## Evidence to Show Jury

- AI preview response JSON
- Task card with status history
- Reminder records endpoint
- Demo acceptance metrics endpoint
