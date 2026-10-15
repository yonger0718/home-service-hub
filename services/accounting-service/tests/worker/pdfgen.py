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


def _write_pdf(objects: list[bytes]) -> bytes:
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


def _stream(body: bytes) -> bytes:
    return b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + body + b"\nendstream"


_TYPE3_PROCS = [b"0 0 750 750 d1 0 0 375 375 re f", b"0 0 750 750 d1 375 375 375 375 re f"]


def _type3_font(proc_ids: tuple[int, int]) -> bytes:
    """A Type3 font with no ToUnicode map and glyph names no glyph list knows: pdfminer renders each glyph as `(cid:N)`,
    the shape of a 遠東/渣打 page."""
    return (b"<< /Type /Font /Subtype /Type3 /FontBBox [0 0 750 750] /FontMatrix [0.001 0 0 0.001 0 0] "
            + f"/CharProcs << /gq {proc_ids[0]} 0 R /gz {proc_ids[1]} 0 R >> ".encode()
            + b"/Encoding << /Type /Encoding /Differences [1 /gq /gz] >> /FirstChar 1 /LastChar 2 /Widths [750 750] /Resources << >> >>")


def _garbage_ops(glyphs: int) -> list[str]:
    ops = ["/F2 11 Tf"]
    for i in range(0, glyphs, 40):
        n = min(40, glyphs - i)
        ops.append("(" + "".join("\\001" if (i + k) % 2 == 0 else "\\002" for k in range(n)) + ") Tj T*")
    return ops


def make_pdf_glyphs(real_lines: list[str], garbage_glyphs: int, *, extra_pages: list = ()) -> bytes:
    """Page 1: Helvetica text plus `garbage_glyphs` unmapped Type3 glyphs. `extra_pages`: further pages, each either a
    list of lines (Helvetica) or a (lines, garbage_glyphs) pair."""
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
                            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",        # 3
                            _stream(_TYPE3_PROCS[0]), _stream(_TYPE3_PROCS[1]),                # 4, 5
                            _type3_font((4, 5))]                                               # 6
    pages = [(real_lines, garbage_glyphs)] + [(pg, 0) if isinstance(pg, list) else tuple(pg) for pg in extra_pages]
    page_ids = []
    for lines, garbage in pages:
        body = ["BT", "/F1 11 Tf", "40 780 Td", "13 TL"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            body.append(f"({safe}) Tj T*")
        body += _garbage_ops(garbage) + ["ET"]
        content = "\n".join(body).encode("latin-1")
        page_num = len(objects) + 1
        page_ids.append(page_num)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 6 0 R >> >> /Contents {page_num + 1} 0 R >>".encode())
        objects.append(_stream(content))
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>".encode()
    return _write_pdf(objects)


def make_pdf_cid(real_text: str, garbage_glyphs: int) -> bytes:
    """One page whose text is drawn with a Type0/CIDFontType2 font (Identity-H, not embedded) that carries a ToUnicode
    CMap — the shape of a 國泰 page: every text glyph maps to Unicode (CJK included) while `garbage_glyphs` Type3
    glyphs (icons) stay unmapped."""
    chars = sorted(set(real_text.replace("\n", "")))
    cid_of = {ch: i + 1 for i, ch in enumerate(chars)}
    bf = "\n".join(f"<{cid:04X}> <{ord(ch):04X}>" for ch, cid in cid_of.items())
    tounicode = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
                 "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n" + f"{len(cid_of)} beginbfchar\n{bf}\nendbfchar\n"
                 + "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend").encode()
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>", b"",
        b"<< /Type /Font /Subtype /Type0 /BaseFont /SynthSans /Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 6 0 R >>",  # 3
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /SynthSans /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 5 0 R /DW 600 >>",  # 4
        b"<< /Type /FontDescriptor /FontName /SynthSans /Flags 32 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 900 /Descent -200 /CapHeight 700 /StemV 80 >>",  # 5
        _stream(tounicode),                                                                       # 6
        _stream(_TYPE3_PROCS[0]), _stream(_TYPE3_PROCS[1]),                                        # 7, 8
        _type3_font((7, 8)),                                                                      # 9
    ]
    body = ["BT", "/F1 11 Tf", "40 780 Td", "13 TL"]
    for line in real_text.split("\n"):
        body.append("<" + "".join(f"{cid_of[ch]:04X}" for ch in line) + "> Tj T*")
    body += _garbage_ops(garbage_glyphs) + ["ET"]
    content = "\n".join(body).encode("latin-1")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R /F2 9 0 R >> >> /Contents 11 0 R >>")  # 10
    objects.append(_stream(content))                                                              # 11
    objects[1] = b"<< /Type /Pages /Kids [10 0 R] /Count 1 >>"
    return _write_pdf(objects)
