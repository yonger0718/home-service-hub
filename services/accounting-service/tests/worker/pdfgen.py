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
