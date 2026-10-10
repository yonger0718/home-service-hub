"""Password candidates per Drive folder from the owner's password file. Values never appear in logs or errors."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

Candidates = dict[str, list[str]]
IDENTITY_KEYS = ("STATEMENT_ID_NUMBER", "STATEMENT_BIRTH_DATE", "STATEMENT_HOLDER_NAMES")
_TOKEN = re.compile(r"\$(ID|BIRTH8|BIRTH6|BIRTH4)(\[(-?\d*)(:)?(-?\d*)\])?$")
_BIRTH_FMT = re.compile(r"\$BIRTH\{([A-Z]+)\}$")


def _strip_comment(value: str) -> str:
    # an inline comment starts with two spaces and '#'; a bare '#' may be part of a password
    return value.split("  #", 1)[0].strip()


def _parse(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    identity: dict[str, str] = {}
    folders: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), _strip_comment(value)
        if key in IDENTITY_KEYS:
            identity[key] = value
        else:
            folders.setdefault(re.sub(r"\.alt\d*$", "", key), []).append(value)
    return identity, folders


def _birth(fmt: str, ymd: str) -> str:
    return fmt.replace("YYYY", ymd[0:4]).replace("YY", ymd[2:4]).replace("MM", ymd[4:6]).replace("DD", ymd[6:8])


def _expand(value: str, identity: dict[str, str]) -> str | None:
    if not value or value.startswith("@"):
        return None
    idn, bd = identity.get("STATEMENT_ID_NUMBER", ""), identity.get("STATEMENT_BIRTH_DATE", "")
    out: list[str] = []
    for part in value.split("+"):
        fmt = _BIRTH_FMT.fullmatch(part)
        if fmt:
            out.append(_birth(fmt.group(1), bd))
            continue
        token = _TOKEN.fullmatch(part)
        if token:
            base = {"ID": idn, "BIRTH8": bd, "BIRTH6": bd[2:], "BIRTH4": bd[4:]}[token.group(1)]
            if token.group(2):
                a, colon, b = token.group(3), token.group(4), token.group(5)
                if colon:
                    base = base[int(a) if a else None:int(b) if b else None]
                else:
                    n = int(a)
                    base = base[n:] if n < 0 else base[n]
            out.append(base)
            continue
        out.append(part)
    joined = "".join(out)
    return joined or None


def load(path: Path) -> tuple[Candidates, str]:
    """{folder key: [candidate passwords in order]} and credential_version (sha256 of the file bytes)."""
    data = path.read_bytes()
    identity, folders = _parse(data.decode("utf-8"))
    out: Candidates = {}
    for folder, values in folders.items():
        out[folder] = [c for c in (_expand(v, identity) for v in values) if c]
    for folder, values in folders.items():
        for v in values:
            if v.startswith("@"):
                out[folder] = out.get(folder, []) + out.get(v[1:].strip(), [])
    return out, hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Identity:
    id_number: str | None
    birth_date: str | None  # YYYYMMDD
    holder_names: list[str]


def identity(path: Path) -> Identity:
    """The three STATEMENT_* fields through the same parser as `load` (whitespace/comment handling identical)."""
    ident, _ = _parse(path.read_text(encoding="utf-8"))
    raw_names = ident.get("STATEMENT_HOLDER_NAMES", "").replace("\\,", "\x00")
    names = [n.replace("\x00", ",").strip() for n in raw_names.split(",") if n.strip()]
    return Identity(ident.get("STATEMENT_ID_NUMBER") or None, ident.get("STATEMENT_BIRTH_DATE") or None, names)


def password_key(root: str, folder: str) -> str:
    """Key in the password file for a source: mail files use their folder path under 銀行/, manual ones 手動下載/<folder>."""
    return folder if root == "mail" else f"手動下載/{folder}"


def describe(candidates: Candidates) -> str:
    """Loggable summary: folder=count, never a value."""
    return ", ".join(f"{folder}={len(values)}" for folder, values in sorted(candidates.items()))
