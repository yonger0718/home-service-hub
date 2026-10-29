"""python -m worker <command>: run | poll | backfill | gate | export-masked | verify | flex | cathay."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import warnings
from datetime import date
from pathlib import Path

import httpx

from worker import cathay, config, flex, pipeline
from worker.runner import SubprocessRunner


def quiet_libraries() -> None:
    """PDF libraries may log fragments of the document; none of that may reach a log or journal."""
    for name in ("pdfminer", "pdfplumber", "pypdf"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    warnings.filterwarnings("ignore", module=r"(pdfminer|pdfplumber|pypdf).*")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    flex.quiet_http_logging()


DEGRADED_CODES = ("parser_disabled", "ocr_unavailable")


def _bad(summary: dict) -> bool:
    """Exit 1 for a failed or aborted run, a disabled parser, or an OCR child the host could not start (its files
    wait for the next run; the unit shows failed so the operator looks)."""
    return bool(summary.get("bad_runs", 0)) or any(code in summary.get("errors", []) for code in DEGRADED_CODES)


def _flex(cfg: config.WorkerConfig, args: argparse.Namespace) -> int:
    if not args.from_file and not cfg.flex_credentials_file.is_file():
        print("flex credentials file is missing or not a file", file=sys.stderr)
        return 2
    try:
        with (httpx.Client(timeout=cfg.flex_timeout_s) as flex_http,
              httpx.Client(base_url=cfg.portfolio_api_url, timeout=cfg.flex_timeout_s) as portfolio_http):
            summary = flex.run(cfg, flex_http=flex_http, portfolio_http=portfolio_http, dry_run=args.dry_run,
                               query_id=args.query_id, from_file=args.from_file, legacy_until=args.legacy_until,
                               unlatch=args.unlatch)
    except flex.AlreadyRunning:
        print("already running")
        return 3
    except flex.FlexDisabled as exc:
        print(f"flex disabled: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:  # the credentials file; the message names the key only
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # the CLI is the output boundary: a class name, never text or a traceback
        print(f"flex failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["codes"] else 0


def _cathay(cfg: config.WorkerConfig, args: argparse.Namespace) -> int:
    from worker.drive import Drive

    others = (args.dry_run, args.file, args.since, args.until, args.retry, args.unlatch, args.validate_history)
    if args.ack and any(others):
        print("--ack cannot be combined with other flags", file=sys.stderr)
        return 2
    try:
        with httpx.Client(base_url=cfg.portfolio_api_url, timeout=cfg.cathay_timeout_s) as portfolio_http:
            summary = cathay.run(cfg, portfolio_http=portfolio_http, drive=Drive(cfg, SubprocessRunner()),
                                 dry_run=args.dry_run, file_id=args.file, since=args.since, until=args.until,
                                 retry_date=args.retry, ack_date=args.ack, unlatch=args.unlatch,
                                 validate_history=args.validate_history)
    except cathay.AlreadyRunning:
        print("already running")
        return 3
    except cathay.CathayDisabled as exc:
        print(f"cathay disabled: {exc}", file=sys.stderr)
        return 1
    except cathay.NothingPending as exc:
        print(f"nothing pending for {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # the CLI is the output boundary: a class name, never text or a traceback
        print(f"cathay failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"counts": summary["counts"]} if args.validate_history else summary, ensure_ascii=False))
    return 1 if summary["codes"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--trigger", choices=["timer", "owner_cli"], default="owner_cli")
    sub.add_parser("poll")
    p_bf = sub.add_parser("backfill")
    p_bf.add_argument("--acknowledge-live-periods", action="store_true")
    sub.add_parser("gate")
    p_em = sub.add_parser("export-masked")
    p_em.add_argument("--for-verify", action="store_true", required=True)
    p_em.add_argument("--folder", default=None)
    p_em.add_argument("--limit", type=int, default=None)
    p_v = sub.add_parser("verify")
    p_v.add_argument("--limit", type=int, default=None)
    p_fx = sub.add_parser("flex")
    p_fx.add_argument("--dry-run", action="store_true")
    p_fx.add_argument("--query-id", default=None)
    p_fx.add_argument("--from-file", type=Path, default=None)
    p_fx.add_argument("--legacy-until", type=date.fromisoformat, default=None)
    p_fx.add_argument("--unlatch", action="store_true")
    p_ct = sub.add_parser("cathay")
    p_ct.add_argument("--dry-run", action="store_true")
    p_ct.add_argument("--file", default=None)
    p_ct.add_argument("--since", type=date.fromisoformat, default=None)
    p_ct.add_argument("--until", type=date.fromisoformat, default=None)
    p_ct.add_argument("--retry", type=date.fromisoformat, default=None)
    p_ct.add_argument("--ack", type=date.fromisoformat, default=None)
    p_ct.add_argument("--unlatch", action="store_true")
    p_ct.add_argument("--validate-history", action="store_true")
    args = ap.parse_args(argv)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    quiet_libraries()
    if args.cmd == "backfill" and not args.acknowledge_live_periods:  # before anything is read or spawned
        print("backfill may touch live periods; pass --acknowledge-live-periods", file=sys.stderr)
        return 2
    try:
        cfg = config.load()
    except config.ConfigError as exc:  # the message names the key and the rule, never the value
        print(f"config: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "flex":
        return _flex(cfg, args)
    if args.cmd == "cathay":
        return _cathay(cfg, args)
    runner = SubprocessRunner()
    if args.cmd == "gate":
        from worker.parser import Gate, Parser

        try:
            with pipeline.singleton(cfg):
                worker_parser = Parser(cfg, runner)
                verify_parser = Parser(cfg, runner, config_dir=cfg.verify_parser_config_dir,
                                       credentials_writable=False) if cfg.verify_parser_config_dir.exists() else None
                report = Gate(cfg).run_operator_gate(worker_parser, verify_parser=verify_parser)
        except pipeline.AlreadyRunning:
            print("already running")
            return 3
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))
        return 0 if report.ok else 1
    if args.cmd in ("export-masked", "verify"):
        from worker import verify
        from worker.parser import ParserDisabled

        try:
            with pipeline.singleton(cfg):
                if args.cmd == "export-masked":
                    result = verify.export_masked(cfg, runner, folder=args.folder, limit=args.limit)
                else:
                    result = verify.run(cfg, runner, limit=args.limit)
                print(json.dumps(result, ensure_ascii=False))
        except pipeline.AlreadyRunning:
            print("already running")
            return 3
        except (pipeline.Refused, verify.Refused) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ParserDisabled as exc:
            print(f"parser disabled: {exc}", file=sys.stderr)
            return 1
        bad = result.get("errors") or result.get("listing_failed") or result.get("parse_failed") or result.get("verify_errors")
        return 1 if bad else 0
    for what, path in (("API token file", cfg.api_token_file), ("password file", cfg.password_file)):
        if not path.is_file():
            print(f"{what} is missing or not a file", file=sys.stderr)
            return 2
    try:
        with pipeline.singleton(cfg), httpx.Client(base_url=cfg.api_url, timeout=60) as client:  # noqa: SIM117
            services = pipeline.build(cfg, client, runner)
            hint = os.environ.get("USER")
            if args.cmd == "run":
                summary = pipeline.run(services, trigger=args.trigger, initiator_hint=hint)
            elif args.cmd == "backfill":
                summary = pipeline.run(services, trigger="owner_cli", mode="backfill", initiator_hint=hint,
                                       acknowledge_live_periods=args.acknowledge_live_periods)
            else:
                summary = pipeline.poll(services, initiator_hint=hint)
            print(json.dumps(summary, ensure_ascii=False))
            # 0 when the run finished done (transient codes in errors included); 1 when a run failed or aborted
            # or finished degraded (poll carries the union of its runs' codes)
            return 1 if _bad(summary) else 0
    except pipeline.AlreadyRunning:
        print("already running")
        return 0
    except pipeline.Refused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except pipeline.RunAborted as exc:
        print(f"run aborted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
