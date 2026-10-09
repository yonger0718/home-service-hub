import re
import unicodedata

_PREFIXES = (
    (re.compile(r"^PAYPAL\s*\*\s*"), "PAYPAL "),
    (re.compile(r"^AMZN\s+MKTP\s*"), "AMAZON "),
    (re.compile(r"^SQ\s*\*\s*"), "SQUARE "),
    (re.compile(r"^GOOGLE\s*\*\s*"), "GOOGLE "),
    (re.compile(r"^APPLE\.COM/BILL\s*"), "APPLE "),
)
_NON_TOKEN = re.compile(r"[^0-9A-Z一-鿿]+")


def normalise(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw or "").upper().strip()
    for pattern, replacement in _PREFIXES:
        text = pattern.sub(replacement, text)
    return " ".join(part for part in _NON_TOKEN.split(text) if part)


def tokens(norm: str) -> frozenset[str]:
    return frozenset(part for part in norm.split(" ") if len(part) >= 2)
