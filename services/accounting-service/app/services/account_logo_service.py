"""Account logo normalisation.

Uploaded bytes are never stored: ``normalise_logo`` turns arbitrary input into a
256x256 RGBA PNG or raises ``LogoError``. Validation order is part of the design:
header-only open -> format allow-list -> side/pixel caps -> only then ``load()``.
"""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_LOGO_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_LOGO_PIXELS = 25_000_000
MIN_SIDE = 16
MAX_SIDE = 8192
OUTPUT_SIDE = 256
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
HASH_PREFIX = 16

# Pillow raises DecompressionBombWarning above this and DecompressionBombError above 2x.
Image.MAX_IMAGE_PIXELS = MAX_LOGO_PIXELS


class LogoError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status if status is not None else (413 if code == "logo_too_large" else 422)


@dataclass(frozen=True)
class NormalisedLogo:
    png: bytes
    sha256: str
    source_media_type: str
    source_bytes: int


def logo_hash(sha256: str) -> str:
    return sha256[:HASH_PREFIX]


def normalise_logo(data: bytes, *, declared_type: str | None = None) -> NormalisedLogo:
    # declared_type is informational only; the media type recorded comes from the decoded format.
    if not data:
        raise LogoError("logo_empty")
    if len(data) > MAX_LOGO_UPLOAD_BYTES:
        raise LogoError("logo_too_large")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as im:
                fmt = im.format
                if fmt not in ALLOWED_FORMATS:
                    raise LogoError("logo_format")
                width, height = im.size
                if (
                    min(width, height) < MIN_SIDE
                    or max(width, height) > MAX_SIDE
                    or width * height > MAX_LOGO_PIXELS
                ):
                    raise LogoError("logo_dimensions")
                media_type = Image.MIME[fmt]

                if fmt == "JPEG":
                    im.draft("RGB", (OUTPUT_SIDE * 2, OUTPUT_SIDE * 2))
                im.load()
                if im.mode == "P":
                    # Pillow resizes palette images with NEAREST; expand first (tRNS is honoured).
                    im = im.convert("RGBA")
                im.thumbnail((OUTPUT_SIDE, OUTPUT_SIDE), Image.LANCZOS, reducing_gap=3.0)
                im = ImageOps.exif_transpose(im)
                im = im.convert("RGBA")

                canvas = Image.new("RGBA", (OUTPUT_SIDE, OUTPUT_SIDE), (0, 0, 0, 0))
                canvas.paste(im, ((OUTPUT_SIDE - im.width) // 2, (OUTPUT_SIDE - im.height) // 2))
                buf = BytesIO()
                canvas.save(buf, format="PNG", optimize=True)
    except LogoError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise LogoError("logo_dimensions") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError):
        raise LogoError("logo_unreadable") from None

    png = buf.getvalue()
    return NormalisedLogo(
        png=png,
        sha256=hashlib.sha256(png).hexdigest(),
        source_media_type=media_type,
        source_bytes=len(data),
    )
