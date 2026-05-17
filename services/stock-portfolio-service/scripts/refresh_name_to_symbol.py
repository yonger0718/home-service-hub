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
