"""Regenerate app/data/name_to_symbol.json from twstock's bundled code table.

twstock ships ~42K codes (TWSE listed + TPEx OTC + warrants + ETFs + ETNs).
Reverse-indexed at module load (see broker_cathay_service._load_name_to_symbol).

Run after `pip install --upgrade twstock` to pick up new listings.
"""

import json
from pathlib import Path

import twstock

OUT = Path(__file__).resolve().parents[1] / "app/data/name_to_symbol.json"
codes = {
    code: info.name.strip()
    for code, info in twstock.codes.items()
    if info.name.strip()
}
OUT.write_text(
    json.dumps(codes, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)
print(f"wrote {len(codes)} entries to {OUT}")

# app/data/name_aliases.json maps old names to codes; a refresh can delist a code or make an alias a current name.
# The service ignores such an alias (with a warning) and the unit suite fails on it, so fix the file now.
aliases = json.loads((OUT.parent / "name_aliases.json").read_text(encoding="utf-8"))
names = set(codes.values())
for alias, code in aliases.items():
    if code not in codes:
        print(f"name_aliases.json: {alias!r} points to {code!r}, which is no longer in the map")
    elif alias in names:
        print(f"name_aliases.json: {alias!r} is now a current name; remove the alias")
