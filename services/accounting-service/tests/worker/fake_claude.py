"""Writes an executable fake `claude` that emits a stream-json transcript (default: a well-behaved parse)."""
import json
import shlex
import stat
from pathlib import Path

GOOD = {"kind": "card", "currency": "TWD", "period_start": "2026-09-01", "period_end": "2026-09-30",
        "closing_date": "2026-09-30", "due_date": "2026-10-15", "opening_balance": "0", "statement_total": "580",
        "minimum_payment": "100", "notes": None,
        "lines": [{"seq": 1, "txn_date": "2026-09-03", "posted_date": "2026-09-04", "merchant_raw": "COFFEE",
                   "printed_amount": "80", "foreign_amount": None, "foreign_currency": None, "line_kind": "purchase",
                   "installment_seq": None, "installment_total": None, "is_subtotal": False},
                  {"seq": 2, "txn_date": "2026-09-10", "posted_date": "2026-09-10", "merchant_raw": "BOOKS",
                   "printed_amount": "500", "foreign_amount": None, "foreign_currency": None, "line_kind": "purchase",
                   "installment_seq": None, "installment_total": None, "is_subtotal": False}]}


class Transcript(str):
    """The stream text plus the timing flags `write` honours (sleep/flood happen after the assistant line)."""
    sleep_after = 0
    flood_after = False


def transcript(output=GOOD, *, tools=("StructuredOutput",), tool_name="StructuredOutput", extra_tool=False,
               num_turns=2, subtype="success", hook_event=False, duplicate_result=False, garbage_line=False,
               message_not_object=False, user_extra_block=False, tool_result_id_mismatch=False,
               assistant_extra_tool_use_after_text=False, sleep_after=0, flood_after=False):
    events = [{"type": "system", "subtype": "init", "tools": list(tools), "claude_code_version": "2.1.296"}]
    if hook_event:
        events.append({"type": "system", "subtype": "hook_started"})
    content = [{"type": "tool_use", "id": "toolu_1", "name": tool_name, "input": output}]
    if extra_tool:
        content.append({"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "id"}})
    if assistant_extra_tool_use_after_text:
        content = [{"type": "text", "text": "ok"}, content[0],
                   {"type": "tool_use", "id": "toolu_3", "name": "StructuredOutput", "input": output}]
    assistant = {"type": "assistant", "message": "x" if message_not_object else {"content": content}}
    events.append(assistant)
    result_block = {"type": "tool_result", "tool_use_id": "toolu_other" if tool_result_id_mismatch else "toolu_1"}
    blocks = [result_block] + ([{"type": "text", "text": "extra"}] if user_extra_block else [])
    events.append({"type": "user", "message": {"content": blocks}})
    result = {"type": "result", "subtype": subtype, "num_turns": num_turns, "is_error": subtype != "success",
              "structured_output": output if subtype == "success" else None}
    if garbage_line:
        events.append("this is not json")
    events.append(result)
    if duplicate_result:
        events.append(result)
    text = "\n".join(e if isinstance(e, str) else json.dumps(e, ensure_ascii=False) for e in events) + "\n"
    out = Transcript(text)
    out.sleep_after, out.flood_after = sleep_after, flood_after
    return out


def _split_after_assistant(body: str) -> tuple[str, str]:
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith('{"type": "assistant"'):
            return "".join(lines[:i + 1]), "".join(lines[i + 1:])
    return body, ""


def write(path: Path, body: str, *, version="2.1.296 (Claude Code)", sleep=0, stdout_bytes=None, exit_code=0,
          stderr_bytes=None, read_stdin=True, grandchild_pid_file=None, close_pipes_then_sleep=None,
          sleep_after=None, flood_after=None) -> Path:
    """`claude --version` prints `version`; any other invocation behaves per the options and prints `body`."""
    sleep_after = sleep_after if sleep_after is not None else getattr(body, "sleep_after", 0)
    flood_after = flood_after if flood_after is not None else getattr(body, "flood_after", False)
    q = lambda p: shlex.quote(str(p))  # noqa: E731
    first, rest = _split_after_assistant(body) if (sleep_after or flood_after) else (body, "")
    part1, part2 = (path.parent / f"{path.name}.out1"), (path.parent / f"{path.name}.out2")
    part1.write_bytes(stdout_bytes if stdout_bytes is not None else first.encode("utf-8"))
    part2.write_bytes(rest.encode("utf-8"))
    script = ["#!/bin/sh", f'if [ "$1" = "--version" ]; then echo {shlex.quote(version)}; exit 0; fi']
    if grandchild_pid_file is not None:
        script.append(f"sleep 60 & echo $! > {q(grandchild_pid_file)}")
    if read_stdin:
        script.append("cat >/dev/null")
    if close_pipes_then_sleep:
        script.append(f"exec >&- 2>&-; sleep {close_pipes_then_sleep}")
    if stderr_bytes is not None:
        err = path.parent / f"{path.name}.err"
        err.write_bytes(stderr_bytes)
        script.append(f"cat {q(err)} >&2")
    script.append(f"sleep {sleep}")
    script.append(f"cat {q(part1)}")
    if sleep_after:
        script.append(f"sleep {sleep_after}")
    if flood_after:
        script.append("head -c 3000000 /dev/zero | tr '\\000' x")
    script.append(f"cat {q(part2)}")
    script.append(f"exit {exit_code}")
    path.write_text("\n".join(script) + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
