## Why

The owner has to remember card debit days and look through HomeHub for open reconciliation work. This change adds a read-only notifier, designed in `docs/superpowers/specs/2026-10-27-accounting-notifications-design.md`: a daily debit reminder and a weekly digest delivered to Discord, with failures delivered by systemd.

## What Changes

- New package `services/accounting-service/notify/` (`python -m notify daily | weekly | failed`): GET-only API client, debit projection with business-day lead time, dedupe state, rendering within the Discord length limit, delivery through `hermes send`.
- Ops files under `services/accounting-service/deploy/notify/`: user units and timers (daily 07:00, Saturday 10:00 Asia/Taipei), an `OnFailure` template unit, `install.sh`, an example config with placeholders only, and `requirements-notify.txt`.
- README section "Notifications (notify/)" with the rollout steps.
- No Alembic revision, no API route, no frontend change; a new read-scope token label `notify` is configured by the owner.

## Capabilities

### New Capabilities

- `accounting-notifications`: the daily debit reminder, the weekly digest, the failure behaviour and the read-only guarantee.

### Modified Capabilities

None.

## Impact

- **Code**: `services/accounting-service/notify/*`, tests in `services/accounting-service/tests/notify/`.
- **Config**: `~/.config/homehub-notify.toml`, token file `~/.config/homehub-notify-token`, API token label `notify` (scope `read`).
- **Data**: none; a small JSON state file outside the repo.
- **Deploy**: two systemd user timers and a failure-delivery template unit for `opc`.
