"""python -m notify daily|weekly [--as-of D] [--dry-run] [--send] [--config P] | failed <unit>.

Exit codes: 0 sent or nothing to send, 1 failure, 2 bad arguments. A failing --send run only writes
`last_failure.txt` (the systemd OnFailure unit delivers it through `failed`).
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import httpx

from . import send
from .api import TZ, ApiError, ReadClient, fetch_daily, fetch_weekly
from .config import ConfigError, NotifyConfig, load_config, read_token
from .cycles import CalendarExhausted
from .debits import build_daily
from .dedupe import apply
from .digest import build_weekly
from .render import render_daily, render_failure, render_weekly
from .state import LastSend, load_state, save_state

DEFAULT_CONFIG = "~/.config/homehub-notify.toml"
FAILURES = (ApiError, CalendarExhausted, ConfigError, httpx.HTTPError, send.SendError)


def failure_path(cfg: NotifyConfig) -> Path:
    return cfg.state_file.with_name("last_failure.txt")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="notify")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("daily", "weekly"):
        s = sub.add_parser(name)
        s.add_argument("--as-of", type=date.fromisoformat, default=None)
        mode = s.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--send", action="store_true")
        s.add_argument("--config", default=DEFAULT_CONFIG)
    f = sub.add_parser("failed")
    f.add_argument("unit")
    f.add_argument("--config", default=DEFAULT_CONFIG)
    return p


def _short(exc: Exception) -> str:
    return f"{type(exc).__name__} {getattr(exc, 'status', None) or getattr(exc, 'reason', None) or ''}".strip()


def _write_failure(cfg: NotifyConfig, line: str) -> None:
    path = failure_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(line)
    os.chmod(path, 0o600)


def _run(args: argparse.Namespace, cfg: NotifyConfig) -> None:
    today = args.as_of or datetime.now(TZ).date()
    token = read_token(cfg.token_file)
    state = load_state(cfg.state_file)
    with httpx.Client(base_url=cfg.api_base, timeout=30) as http:
        client = ReadClient(http, token)
        if args.command == "daily":
            report, state, keys = apply(build_daily(fetch_daily(client, today), cfg), state, today)
            messages = render_daily(report, cfg)
            items = sum(len(b.items) for b in report.blocks) + len(report.manual)
            last = state.last_send
            if last is not None and last.day == today and last.triggers == keys:
                messages = []  # same triggers already sent today
            elif messages:
                state.last_send = LastSend(today, keys)
        else:
            messages = render_weekly(build_weekly(fetch_weekly(client, today), cfg, state.last_daily_run), cfg)
    if not args.send:
        print("\n---\n".join(messages))
        return
    send.deliver(messages, cfg.discord_target)
    now = datetime.now(TZ)
    state = replace(state, last_daily_run=now) if args.command == "daily" else replace(state, last_weekly_run=now)
    save_state(cfg.state_file, state)
    noun = "message" if len(messages) == 1 else "messages"
    if args.command == "daily":
        print(f"daily: {items} items, sent {len(messages)} {noun}" if messages else "daily: nothing to send",
              file=sys.stderr)
    else:
        print(f"weekly: sent {len(messages)} {noun}", file=sys.stderr)


def _failed(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(Path(args.config).expanduser())
        path = failure_path(cfg)
        text = None
        try:
            text = path.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        text = text or render_failure(f"{args.unit} 非正常結束，請看 journalctl --user -u {args.unit}")
        send.deliver([text], cfg.discord_target)
        path.unlink(missing_ok=True)
    except (ConfigError, send.SendError, OSError) as exc:
        print(f"failed: {type(exc).__name__}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if args.command == "failed":
        return _failed(args)
    cfg = None
    try:
        cfg = load_config(Path(args.config).expanduser())
        _run(args, cfg)
    except FAILURES as exc:
        line = render_failure(_short(exc))
        print(line, file=sys.stderr)
        if args.send and cfg is not None:
            _write_failure(cfg, line)
        return 1
    return 0
