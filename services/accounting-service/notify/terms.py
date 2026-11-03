"""The `terms` command: build input, run Hermes, parse, render, send; every run leaves a 0700 archive."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from . import send
from .api import ReadClient
from .config import ConfigError, NotifyConfig, read_token
from .hermes_run import HERMES_ENV_KEYS, run_hermes
from .terms_input import build_input, select_cards, to_json
from .terms_parse import TermsParseError, digest, join_rules, parse_result
from .terms_prompt import build_prompt
from .terms_render import has_items, render_terms
from .terms_state import (CardSeen, TermsStateError, decide, load_terms_state, save_terms_state,
                          terms_lock)


class TermsArchiveError(Exception):
    """The run's archive dir already exists (timestamp collision); carries the path only."""

    reason = "archive"

    def __init__(self, archive: Path):
        super().__init__(f"archive {archive}")
        self.archive = archive


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_archive(report_dir: Path, now: datetime) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = report_dir / now.strftime("%Y%m%dT%H%M%SZ")
    try:
        archive.mkdir(mode=0o700)
    except FileExistsError:
        raise TermsArchiveError(archive) from None  # never reuse or overwrite a run
    return archive


def _summary(inp, decisions, sent: int) -> None:
    shown = sum(has_items(d) for d in decisions.values())
    print(f"terms: {len(inp.cards)} cards, {shown} with items, sent {sent} messages", file=sys.stderr)


def run_terms(args: argparse.Namespace, cfg: NotifyConfig, today: date) -> list[str]:
    t = cfg.terms
    if t is None:
        raise ConfigError("terms")
    if not select_cards(t, args.cause):
        print(f"terms: no cards for --cause {args.cause}", file=sys.stderr)
        return []
    token = read_token(cfg.token_file)  # stays in the API client; never in the archive, prompt or Hermes env
    with httpx.Client(base_url=cfg.api_base, timeout=30) as http:
        inp = build_input(ReadClient(http, token), t, today, args.cause)
    now = _utcnow()
    archive = _make_archive(t.report_dir, now)
    _write(archive / "input.json", json.dumps(to_json(inp), ensure_ascii=False, indent=2))
    _write(archive / "prompt.md", build_prompt(inp))
    stdout = run_hermes(
        prompt_path=archive / "prompt.md", toolsets=t.hermes_toolsets, timeout_s=t.hermes_timeout_seconds,
        model=t.hermes_model, provider=t.hermes_provider,
        env={k: os.environ[k] for k in HERMES_ENV_KEYS if k in os.environ},
        cwd=archive, max_output_bytes=t.max_output_bytes, archive=archive).stdout
    try:
        result = parse_result(stdout, tuple(c.card for c in inp.cards))
    except TermsParseError as exc:
        exc.archive = archive
        raise
    result = join_rules(result, {c.card.account_id: c.rules for c in inp.cards})  # system side = HomeHub rows
    _write(archive / "result.json", json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2))
    tiers = {c.card.account_id: c.card for c in inp.cards}
    digests = {c.account_id: digest(c, tiers[c.account_id].tier) for c in result.cards}
    try:
        with terms_lock(t.terms_state_file):
            state = load_terms_state(t.terms_state_file)
            decisions = {c.account_id: decide(c, tiers[c.account_id], state.cards.get(c.account_id),
                                              digests[c.account_id]) for c in result.cards}
            messages = render_terms(inp, result, decisions, cfg, archive)
            _write(archive / "message.md", "\n\n---\n\n".join(messages))
            if not args.send:
                print("\n---\n".join(messages))
                _summary(inp, decisions, 0)
                return messages
            send.deliver(messages, cfg.discord_target)
            for c in result.cards:
                state.cards[c.account_id] = CardSeen(digests[c.account_id], now)
            save_terms_state(t.terms_state_file, state)
    except TermsStateError as exc:
        exc.archive = archive
        raise
    (cfg.state_file.with_name("last_failure.txt")).unlink(missing_ok=True)
    _summary(inp, decisions, len(messages))
    return messages
