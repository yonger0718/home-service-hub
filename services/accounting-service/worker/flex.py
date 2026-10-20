"""IB Flex Web Service client pieces: the credentials file reader (the token never leaves this module's values)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TOKEN_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")
QUERY_RE = re.compile(r"^[0-9]{1,12}$")


@dataclass(frozen=True)
class Credentials:
    token: str = field(repr=False)
    query_id: str


def load_credentials(path: Path) -> Credentials:
    """Parse `key=value` lines (BOM, `#` comments and blank lines allowed); a bad value names its key only."""
    values: dict[str, str] = {}
    for line in path.read_bytes().decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    token = values.get("IB_FLEX_TOKEN", "")
    if not TOKEN_RE.match(token):
        raise ValueError("IB_FLEX_TOKEN missing or malformed")
    query_id = values.get("IB_FLEX_QUERY_ID", "")
    if not QUERY_RE.match(query_id):
        raise ValueError("IB_FLEX_QUERY_ID missing or malformed")
    return Credentials(token=token, query_id=query_id)
