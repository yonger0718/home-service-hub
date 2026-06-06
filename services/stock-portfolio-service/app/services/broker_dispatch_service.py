from __future__ import annotations

import csv
import io

from ..models import portfolio as models

SCHWAB_HEADER = [
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "Price",
    "Fees & Comm",
    "Amount",
]


def sniff(raw_bytes: bytes) -> models.Broker | None:
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    lines = [line for line in text.splitlines()[:5] if line.strip()]
    if not lines:
        return None
    if lines[0].startswith("Statement,Header,域名稱,域值"):
        return models.Broker.IB
    for line in lines:
        try:
            row = next(csv.reader(io.StringIO(line)))
        except csv.Error:
            continue
        cells = {cell.strip() for cell in row}
        if {"交易類別", "代號"} <= cells:
            return models.Broker.FIRSTRADE
        if row == SCHWAB_HEADER:
            return models.Broker.SCHWAB
    return None
