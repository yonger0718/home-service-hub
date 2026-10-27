#!/usr/bin/env bash
# Install the HomeHub notifier user units and enable both timers. Idempotent.
# Does not create the release checkout or the venv (see services/accounting-service/README.md).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dest="${HOME}/.config/systemd/user"
mkdir -p "$dest"
for unit in homehub-notify-daily.service homehub-notify-daily.timer \
            homehub-notify-weekly.service homehub-notify-weekly.timer \
            'homehub-notify-failed@.service'; do
  install -m 0644 "$here/$unit" "$dest/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now homehub-notify-daily.timer homehub-notify-weekly.timer
systemctl --user list-timers --all | grep homehub-notify || true
