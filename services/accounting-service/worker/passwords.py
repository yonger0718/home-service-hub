"""Password candidates per Drive folder from the owner's password file. Values never appear in logs or errors."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

Candidates = dict[str, list[str]]
IDENTITY_KEYS = ("STATEMENT_ID_NUMBER", "STATEMENT_BIRTH_DATE", "STATEMENT_HOLDER_NAMES")
_TOKEN = re.compile(r"\$(ID|BIRTH8|BIRTH6|BIRTH4)(\[(-?\d*)(:)?(-?\d*)\])?$")
_BIRTH_FMT = re.compile(r"\$BIRTH\{([A-Z]+)\}$")


def _read(path: Path) -> tuple[str, bytes]:
    """Decoded text (BOM stripped) and the raw bytes; the file is read once."""
    data = path.read_bytes()
    return data.decode("utf-8-sig"), data


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


def _slice(base: str, a: str, colon: str | None, b: str) -> str:
    if colon:
        return base[int(a) if a else None:int(b) if b else None]
    if not a:
        raise ValueError
    n = int(a)
    return base[n:] if n < 0 else base[n]


def _expand(value: str, identity: dict[str, str], key: str) -> str | None:
    """One rule to one candidate; None when the rule needs an identity field that is absent."""
    if not value:
        return None
    idn, bd = identity.get("STATEMENT_ID_NUMBER", ""), identity.get("STATEMENT_BIRTH_DATE", "")
    out: list[str] = []
    try:
        for part in value.split("+"):
            fmt = _BIRTH_FMT.fullmatch(part)
            if fmt:
                if not bd:
                    return None
                out.append(_birth(fmt.group(1), bd))
                continue
            token = _TOKEN.fullmatch(part)
            if token:
                base = {"ID": idn, "BIRTH8": bd, "BIRTH6": bd[2:], "BIRTH4": bd[4:]}[token.group(1)]
                if not base:
                    return None
                if token.group(2):
                    base = _slice(base, token.group(3), token.group(4), token.group(5))
                out.append(base)
                continue
            out.append(part)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"bad rule in {key}") from exc
    joined = "".join(out)
    return joined or None


def load(path: Path) -> tuple[Candidates, str]:
    """{folder key: [candidate passwords in order]} and credential_version (sha256 of the file bytes)."""
    text, data = _read(path)
    identity, folders = _parse(text)
    resolved: Candidates = {}

    def resolve(key: str, stack: tuple[str, ...]) -> list[str]:
        if key in resolved:
            return resolved[key]
        if key in stack:
            raise ValueError(f"alias cycle at {key}")
        values = folders.get(key)
        if values is None:
            return []
        out: list[str] = []
        aliases: list[str] = []
        for v in values:
            if v.startswith("@"):
                aliases.append(v[1:].strip())
            else:
                candidate = _expand(v, identity, key)
                if candidate:
                    out.append(candidate)
        for target in aliases:
            if target == key or target in stack:
                raise ValueError(f"alias cycle at {key}")
            out.extend(resolve(target, stack + (key,)))
        resolved[key] = out
        return out

    cands: Candidates = {key: resolve(key, ()) for key in folders}
    return cands, hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Identity:
    id_number: str | None = field(repr=False)
    birth_date: str | None = field(repr=False)  # YYYYMMDD
    holder_names: list[str] = field(repr=False)


def identity(path: Path) -> Identity:
    """The three STATEMENT_* fields through the same parser as `load` (whitespace/comment handling identical)."""
    text, _ = _read(path)
    ident, _ = _parse(text)
    raw_names = ident.get("STATEMENT_HOLDER_NAMES", "").replace("\\,", "\x00")
    names = [n.replace("\x00", ",").strip() for n in raw_names.split(",") if n.strip()]
    return Identity(ident.get("STATEMENT_ID_NUMBER") or None, ident.get("STATEMENT_BIRTH_DATE") or None, names)


def password_key(root: str, folder: str) -> str:
    """Key in the password file for a source: mail files use their folder path under 銀行/, manual ones 手動下載/<folder>."""
    return folder if root == "mail" else f"手動下載/{folder}"


def describe(candidates: Candidates) -> str:
    """Loggable summary: folder=count, never a value."""
    return ", ".join(f"{folder}={len(values)}" for folder, values in sorted(candidates.items()))
