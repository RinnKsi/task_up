# Notification and Escalation Policy

## Objective

Deliver reminders early enough to prevent overdue tasks and escalate only when needed.

## Channels

1. Telegram (primary)
2. Web inbox/dashboard indicators (secondary)

## Reminder Windows

- T-24h before deadline
- T-2h before deadline
- Immediate overdue escalation when deadline passed

## Stop Conditions

- Task status is `done`
- Task is archived/cancelled (future extension)
- Reminder already created and still unsent for same task/user

## Anti-Duplicate Rule

Only one active unsent reminder per `(task_id, user_id, channel)`.

## Escalation Rule

- Student gets pre-deadline reminders.
- Parent gets overdue escalation reminders.
- Teacher dashboard shows overdue counters and priority list.

## Failure Handling

- Telegram unavailable: keep reminder record unsent for retry.
- Scheduler retry uses periodic APScheduler job.
