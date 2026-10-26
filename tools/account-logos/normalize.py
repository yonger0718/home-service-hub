#!/usr/bin/env python3
"""Normalise one downloaded institution logo into the SPA's bundled logo set (#175).

Usage: python3 -I tools/account-logos/normalize.py <in.svg|in.png> <out-dir> <slug>

Downloads are untrusted. SVG: size cap, a leading `<?xml …?>` declaration dropped, forbidden text / regex
(svg-rules.json, case-insensitive; `<?` there rejects every other processing instruction) checked before parsing;
then every element and attribute is checked (forbidden elements, on* handlers, non-fragment hrefs, any
`forbidden_attr_chars` such as a CSS-escape backslash in a value or text), <metadata> and editor- / foreign-
namespace elements and attributes are dropped, and the mark is wrapped in a
square <svg viewBox="0 0 S S"> with S = max(w, h) * 1.16 so it sits centred with 8 % padding on the long side.
PNG: size cap, signature, square IHDR >= 180 px; re-written with only the IHDR/PLTE/tRNS/IDAT/IEND chunks.
The output is <out-dir>/<slug>.<sha256[:8]>.<svg|png> (content-hashed, as the SPA publisher keeps every
hashed asset immutable: a changed mark gets a new name; update the manifest's `file` and delete the old file).
Prints the output path; exits 1 with a reason on any rejection. Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parent / "svg-rules.json").read_text(encoding="utf-8"))
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"
KEPT_ATTR_NS = {XLINK_NS, XML_NS}
SLUG = re.compile(r"^[a-z0-9-]{1,32}$")
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_KEEP = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}
XML_DECL = re.compile(r"^\s*<\?xml\s[^?]*\?>")
NUMBER = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*(px)?\s*$")


class Rejected(Exception):
    pass


def split(name: str) -> tuple[str | None, str]:
    """('{ns}local') -> (ns, local); a bare name -> (None, name)."""
    if name.startswith("{"):
        ns, local = name[1:].split("}", 1)
        return ns, local
    return None, name


def fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def check_text(text: str) -> None:
    low = text.lower()
    for t in RULES["forbidden_text"]:
        if t.lower() in low:
            raise Rejected(f"forbidden text {t!r}")
    for r in RULES["forbidden_regex"]:
        m = re.search(r, text, re.IGNORECASE)
        if m:
            raise Rejected(f"forbidden pattern {r!r} at offset {m.start()}: {text[m.start():m.start() + 40]!r}")


def length(value: str | None, what: str) -> float:
    m = NUMBER.match(value or "")
    if not m:
        raise Rejected(f"{what} {value!r} is not an absolute unitless / px number")
    return float(m.group(1))


def check_chars(value: str | None, where: str) -> None:
    for c in RULES["forbidden_attr_chars"]:
        if value and c in value:
            raise Rejected(f"forbidden character {c!r} in {where}: {value[:60]!r}")


def sanitize(el: ET.Element) -> None:
    """Reject unsafe content below (and including) `el`; drop metadata and editor-namespace nodes."""
    ns, local = split(el.tag)
    if local in RULES["forbidden_elements"]:
        raise Rejected(f"forbidden element <{local}>")
    check_chars(el.text, f"<{local}> text")
    for child in el:
        check_chars(child.tail, f"<{local}> text")
    for name in list(el.attrib):
        ans, alocal = split(name)
        if alocal.lower().startswith("on"):
            raise Rejected(f"event-handler attribute {alocal!r} on <{local}>")
        if alocal == "href" and not el.attrib[name].startswith("#"):
            raise Rejected(f"non-fragment href {el.attrib[name][:60]!r} on <{local}>")
        if ans is not None and ans not in KEPT_ATTR_NS:
            del el.attrib[name]  # inkscape:/sodipodi:/i: … editor attributes
            continue
        if ans == XML_NS and alocal == "base":
            del el.attrib[name]  # xml:base would retarget fragment hrefs / url(#…) away from this document
            continue
        check_chars(el.attrib[name], f"<{local} {alocal}>")
    for child in list(el):
        if not isinstance(child.tag, str):
            el.remove(child)
            continue
        cns, clocal = split(child.tag)
        if clocal == "metadata" or cns != SVG_NS:
            el.remove(child)  # metadata, sodipodi:namedview, Illustrator private data …
            continue
        sanitize(child)


def normalize_svg(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise Rejected(f"not UTF-8: {e}") from None
    text = XML_DECL.sub("", text, count=1)
    check_text(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise Rejected(f"XML parse error: {e}") from None
    if root.tag != f"{{{SVG_NS}}}svg":
        raise Rejected(f"root element is {root.tag!r}, not an SVG-namespace <svg>")
    sanitize(root)

    vb = root.get("viewBox")
    if vb is not None:
        parts = re.split(r"[\s,]+", vb.strip())
        if len(parts) != 4:
            raise Rejected(f"viewBox {vb!r} does not have four numbers")
        if any((m := NUMBER.match(v)) is None or m.group(2) for v in parts):
            raise Rejected(f"viewBox {vb!r} must be four unitless numbers")
        w, h = float(parts[2]), float(parts[3])
    else:
        w = length(root.get("width"), "width")
        h = length(root.get("height"), "height")
        root.set("viewBox", f"0 0 {fmt(w)} {fmt(h)}")
    if w <= 0 or h <= 0:
        raise Rejected(f"non-positive size {w} x {h}")

    style = root.get("style")
    if style is not None:
        kept = [d for d in style.split(";") if d.strip() and d.split(":", 1)[0].strip().lower() not in ("width", "height")]
        if kept:
            root.set("style", ";".join(d.strip() for d in kept))
        else:
            del root.attrib["style"]

    s = round(max(w, h) * 1.16, 4)
    for a in ("x", "y", "width", "height"):
        root.attrib.pop(a, None)
    root.set("x", fmt((s - w) / 2))
    root.set("y", fmt((s - h) / 2))
    root.set("width", fmt(w))
    root.set("height", fmt(h))

    wrapper = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": f"0 0 {fmt(s)} {fmt(s)}"})
    wrapper.append(root)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    result = ET.tostring(wrapper, encoding="unicode", short_empty_elements=True) + "\n"
    check_text(result)
    encoded = result.encode("utf-8")
    if len(encoded) > RULES["max_bytes"]:
        raise Rejected(f"output is {len(encoded)} bytes > {RULES['max_bytes']}")
    return encoded


def normalize_png(data: bytes) -> bytes:
    if not data.startswith(PNG_SIG):
        raise Rejected("not a PNG (bad signature)")
    pos, chunks = len(PNG_SIG), []
    while pos < len(data):
        if pos + 8 > len(data):
            raise Rejected("truncated chunk header")
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + size]
        if len(body) != size or pos + 12 + size > len(data):
            raise Rejected(f"truncated {kind!r} chunk")
        (crc,) = struct.unpack(">I", data[pos + 8 + size:pos + 12 + size])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise Rejected(f"bad CRC in {kind!r} chunk")
        chunks.append((kind, body))
        pos += 12 + size
        if kind == b"IEND":
            break
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise Rejected("first chunk is not a valid IHDR")
    if chunks[-1][0] != b"IEND":
        raise Rejected("no IEND chunk")
    w, h = struct.unpack(">II", chunks[0][1][:8])
    if w != h or w < 180:
        raise Rejected(f"PNG is {w} x {h}; need square >= 180 px")
    if not any(k == b"IDAT" for k, _ in chunks):
        raise Rejected("no IDAT chunk")
    buf = bytearray(PNG_SIG)
    for kind, body in chunks:
        if kind in PNG_KEEP:
            buf += struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return bytes(buf)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("slug")
    args = p.parse_args(argv)
    try:
        if not SLUG.match(args.slug):
            raise Rejected(f"slug {args.slug!r} does not match {SLUG.pattern}")
        data = args.input.read_bytes()
        if len(data) > RULES["max_bytes"]:
            raise Rejected(f"input is {len(data)} bytes > {RULES['max_bytes']}")
        is_png = data.startswith(PNG_SIG)
        result = (normalize_png if is_png else normalize_svg)(data)
        digest = hashlib.sha256(result).hexdigest()[:8]
        out = args.out_dir / f"{args.slug}.{digest}.{'png' if is_png else 'svg'}"
        out.write_bytes(result)
    except Rejected as e:
        print(f"rejected {args.input}: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
