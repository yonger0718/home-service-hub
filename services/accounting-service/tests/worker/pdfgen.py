"""Tiny synthetic PDFs for worker tests: ASCII text on one or more pages, optionally AES-encrypted."""
from io import BytesIO

from pypdf import PdfReader, PdfWriter


def _page_stream(lines: list[str]) -> bytes:
    body = ["BT", "/F1 11 Tf", "40 780 Td", "13 TL"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        body.append(f"({safe}) Tj T*")
    body.append("ET")
    return "\n".join(body).encode("latin-1")


def make_pdf(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []
    page_ids = []
    font_id = 3
    kids_start = 4
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # pages, filled below
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content = _page_stream(lines)
        page_num = kids_start + 2 * i
        page_ids.append(page_num)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_num + 1} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def encrypt(data: bytes, password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def statement_lines(n: int = 25) -> list[str]:
    """Enough ASCII to pass the 200-character page-1 probe."""
    return [f"2026/09/{(i % 28) + 1:02d} MERCHANT {i:03d} NT$ {100 + i}.00" for i in range(n)]


def make_pdf_glyphs(real_lines: list[str], garbage_glyphs: int, *, extra_pages: list[list[str]] = ()) -> bytes:
    """One page of Helvetica text plus `garbage_glyphs` glyphs from a Type3 font that has no ToUnicode map and glyph
    names no glyph list knows, so pdfminer renders each of them as `(cid:N)` — the shape of a 遠東/渣打 page 1
    (R5.1). Further plain pages may follow."""
    procs = ["0 0 750 750 d1 0 0 375 375 re f", "0 0 750 750 d1 375 375 375 375 re f"]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # pages
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")                       # 3
    objects.append(b"<< /Length " + str(len(procs[0])).encode() + b" >>\nstream\n" + procs[0].encode() + b"\nendstream")  # 4
    objects.append(b"<< /Length " + str(len(procs[1])).encode() + b" >>\nstream\n" + procs[1].encode() + b"\nendstream")  # 5
    objects.append(b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 750 750] /FontMatrix [0.001 0 0 0.001 0 0] "
                   b"/CharProcs << /gq 4 0 R /gz 5 0 R >> /Encoding << /Type /Encoding /Differences [1 /gq /gz] >> "
                   b"/FirstChar 1 /LastChar 2 /Widths [750 750] /Resources << >> >>")                # 6
    body = ["BT", "/F1 11 Tf", "40 780 Td", "13 TL"]
    for line in real_lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        body.append(f"({safe}) Tj T*")
    body.append("/F2 11 Tf")
    for i in range(0, garbage_glyphs, 40):
        n = min(40, garbage_glyphs - i)
        body.append("(" + "".join("\\001" if (i + k) % 2 == 0 else "\\002" for k in range(n)) + ") Tj T*")
    body.append("ET")
    content = "\n".join(body).encode("latin-1")
    pages = [(content, b"/F1 3 0 R /F2 6 0 R")] + [(_page_stream(lines), b"/F1 3 0 R") for lines in extra_pages]
    page_ids = []
    for content, fonts in pages:
        page_num = len(objects) + 1
        page_ids.append(page_num)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << {fonts.decode()} >> >> /Contents {page_num + 1} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
