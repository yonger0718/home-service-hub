"""python -m worker <command>: run | poll | backfill | gate | export-masked | verify."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import httpx

from worker import config, pipeline
from worker.runner import SubprocessRunner


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
    args = ap.parse_args(argv)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.cmd == "backfill" and not args.acknowledge_live_periods:  # before anything is read or spawned
        print("backfill may touch live periods; pass --acknowledge-live-periods", file=sys.stderr)
        return 2
    cfg = config.load()
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
            return 0
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
            return 0
        except (pipeline.Refused, verify.Refused) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ParserDisabled as exc:
            print(f"parser disabled: {exc}", file=sys.stderr)
            return 1
        return 1 if result.get("errors") or result.get("listing_failed") else 0
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
            bad = summary.get("bad_runs", 0) or ("parser_disabled" in summary.get("errors", []))
            return 1 if bad else 0
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
