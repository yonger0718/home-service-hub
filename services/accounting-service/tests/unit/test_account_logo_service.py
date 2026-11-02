import hashlib
import os
import subprocess
import sys
from io import BytesIO

from unittest import mock

import pytest
from PIL import Image, ImageOps, JpegImagePlugin

from app.services.account_logo_service import LogoError, normalise_logo

PEAK_RSS_BUDGET_BYTES = 6 * 64 * 1024 * 1024  # VmHWM measured RGBA 236519424, LA 236519424, P 258080768; max x1.5 -> next 64 MiB


def _img(w, h, fmt="PNG", mode="RGBA", color=None, **save_kw) -> bytes:
    if color is None:
        color = {"RGBA": (10, 120, 200, 255), "RGB": (10, 120, 200), "LA": (100, 255),
                 "L": 100, "P": 1, "1": 1}.get(mode, 0)
    im = Image.new(mode, (w, h), color)
    buf = BytesIO()
    im.save(buf, format=fmt, **save_kw)
    return buf.getvalue()


def _jpeg_with_orientation(orientation: int, size=(200, 100)) -> bytes:
    w, h = size
    im = Image.new("RGB", (w, h), (255, 0, 0))
    im.paste((0, 0, 255), (w // 2, 0, w, h))
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = BytesIO()
    im.save(buf, format="JPEG", exif=exif, quality=95)
    return buf.getvalue()


def _code(data, **kw):
    with pytest.raises(LogoError) as e:
        normalise_logo(data, **kw)
    return e.value


def test_rejects_empty():
    e = _code(b"")
    assert (e.code, e.status) == ("logo_empty", 422)


def test_rejects_too_large():
    e = _code(b"x" * (5 * 1024 * 1024 + 1))
    assert (e.code, e.status) == ("logo_too_large", 413)


def test_rejects_non_image_text():
    e = _code(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert (e.code, e.status) == ("logo_unreadable", 422)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_rejects_other_formats(fmt):
    assert _code(_img(100, 100, fmt=fmt, mode="RGB")).code == "logo_format"


@pytest.mark.parametrize("w,h", [(15, 100), (100, 15), (8193, 20), (20, 8193)])
def test_rejects_sides(w, h):
    assert _code(_img(w, h, mode="L")).code == "logo_dimensions"


def test_rejects_pixel_count_in_pillow_warning_band():
    assert _code(_img(6000, 6000, mode="L")).code == "logo_dimensions"


def test_rejects_pixel_count_in_pillow_error_band():
    assert _code(_img(9000, 9000, mode="L")).code == "logo_dimensions"


def test_output_is_256_rgba_png_with_transparent_padding():
    out = normalise_logo(_img(300, 100, color=(255, 0, 0, 255)))
    im = Image.open(BytesIO(out.png))
    assert (im.format, im.mode, im.size) == ("PNG", "RGBA", (256, 256))
    assert im.getpixel((128, 2))[3] == 0 and im.getpixel((128, 128))[:3] == (255, 0, 0)
    assert "exif" not in im.info and out.sha256 == hashlib.sha256(out.png).hexdigest()
    assert out.source_media_type == "image/png" and out.source_bytes == len(_img(300, 100, color=(255, 0, 0, 255)))


@pytest.mark.parametrize("w,h,expect", [(8192, 16, (256, 1)), (16, 8192, (1, 256))])
def test_extreme_aspect_shrinks_first(w, h, expect):
    out = normalise_logo(_img(w, h, mode="RGB"))
    im = Image.open(BytesIO(out.png))
    bbox = im.getchannel("A").point(lambda a: 255 if a > 0 else 0).getbbox()
    assert (bbox[2] - bbox[0], bbox[3] - bbox[1]) == expect
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    assert abs(cx - 128) <= 1 and abs(cy - 128) <= 1


def test_exif_orientation_is_applied():
    out = normalise_logo(_jpeg_with_orientation(6))
    im = Image.open(BytesIO(out.png)).convert("RGBA")
    top, bottom = im.getpixel((128, 80)), im.getpixel((128, 176))
    assert top[0] > 200 and top[2] < 60, top
    assert bottom[2] > 200 and bottom[0] < 60, bottom


def test_palette_transparency_survives():
    im = Image.new("P", (100, 100), 0)
    im.putpalette([255, 0, 0, 0, 255, 0] + [0, 0, 0] * 254)
    im.paste(1, (50, 0, 100, 100))
    buf = BytesIO()
    im.save(buf, format="PNG", transparency=0)
    out = normalise_logo(buf.getvalue())
    res = Image.open(BytesIO(out.png))
    assert res.getpixel((128 - 40, 128))[3] == 0
    assert res.getpixel((128 + 40, 128))[3] == 255


def test_webp_accepted_and_jpeg_draft_used(monkeypatch):
    out = normalise_logo(_img(300, 300, fmt="WEBP", mode="RGB"))
    assert out.source_media_type == "image/webp"
    jpeg = _img(3000, 3000, fmt="JPEG", mode="RGB")
    order = []
    real_draft, real_load = JpegImagePlugin.JpegImageFile.draft, Image.Image.load
    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", lambda self, *a, **k: order.append("draft") or real_draft(self, *a, **k))
    monkeypatch.setattr(Image.Image, "load", lambda self, *a, **k: order.append("load") or real_load(self, *a, **k))
    normalise_logo(jpeg)
    assert "draft" in order and order.index("draft") < order.index("load")


def test_same_bytes_same_hash():
    data = _img(300, 100)
    assert normalise_logo(data).sha256 == normalise_logo(data).sha256


def test_logo_hash_is_16_hex_prefix():
    from app.services.account_logo_service import logo_hash
    assert logo_hash("ab" * 32) == "ab" * 8


def test_declared_type_is_recorded_not_trusted():
    out = normalise_logo(_img(100, 100), declared_type="image/svg+xml")
    assert out.source_media_type == "image/png"


def test_translucent_pixels_keep_their_alpha_and_colour():
    out = normalise_logo(_img(256, 256, color=(0, 0, 255, 128)))
    px = Image.open(BytesIO(out.png)).getpixel((128, 128))
    assert px[:3] == (0, 0, 255) and 124 <= px[3] <= 132


def test_product_gate_holds_without_pillow_bomb_checks(monkeypatch):
    big = _img(6000, 6000, mode="L")  # build BEFORE patching
    calls = []
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)
    monkeypatch.setattr(Image.Image, "load", lambda self: calls.append("load") or None)
    assert _code(big).code == "logo_dimensions"
    assert calls == []


def test_large_palette_image_is_not_nearest_resampled():
    im = Image.new("P", (1024, 1024), 0)
    im.putpalette([0, 0, 0, 255, 0, 0, 0, 0, 255] + [0, 0, 0] * 253)
    px = im.load()
    for y in range(1024):
        for x in range(512, 1024):
            px[x, y] = 1 + ((x // 4 + y // 4) % 2)
    buf = BytesIO()
    im.save(buf, format="PNG", transparency=0)
    out = Image.open(BytesIO(normalise_logo(buf.getvalue()).png))
    assert out.getpixel((20, 128))[3] == 0
    right = [out.getpixel((x, y)) for x in range(140, 250) for y in range(10, 250)]
    assert any(p[0] not in (0, 255) and p[2] not in (0, 255) for p in right)


def test_rejected_inputs_never_decode_pixels(monkeypatch):
    fixtures = [_img(100, 100, fmt="GIF", mode="RGB"), _img(15, 15), _img(6000, 6000, mode="L")]
    calls = []
    monkeypatch.setattr(Image.Image, "load", lambda self: calls.append("load") or None)
    for bad in fixtures:
        with pytest.raises(LogoError):
            normalise_logo(bad)
    assert calls == []


def test_corrupt_pixel_data_is_unreadable():
    good = _img(300, 100)
    corrupt = good[: len(good) // 2]
    assert _code(corrupt).code == "logo_unreadable"


def test_shrink_happens_before_transpose(monkeypatch):
    order = []
    real_thumb, real_tr = Image.Image.thumbnail, ImageOps.exif_transpose
    monkeypatch.setattr(Image.Image, "thumbnail", lambda self, *a, **k: order.append("thumbnail") or real_thumb(self, *a, **k))
    monkeypatch.setattr(ImageOps, "exif_transpose", lambda im, *a, **k: order.append("transpose") or real_tr(im, *a, **k))
    normalise_logo(_jpeg_with_orientation(6, size=(600, 300)))
    assert order == ["thumbnail", "transpose"]


def _opaque_bbox(png: bytes):
    return Image.open(BytesIO(png)).getchannel("A").point(lambda a: 255 if a > 0 else 0).getbbox()


def test_small_square_input_is_enlarged_to_fill_the_tile():
    assert _opaque_bbox(normalise_logo(_img(32, 32)).png) == (0, 0, 256, 256)


def test_small_wide_input_is_enlarged_to_fit_and_centred():
    left, top, right, bottom = _opaque_bbox(normalise_logo(_img(180, 60)).png)
    assert (left, right) == (0, 256)
    assert bottom - top in (85, 86)
    assert abs(top - (256 - bottom)) <= 1


def test_large_input_uses_thumbnail_shrink_path():
    with mock.patch.object(Image.Image, "thumbnail", autospec=True, side_effect=Image.Image.thumbnail) as spy:
        out = normalise_logo(_img(300, 300))
    assert spy.called
    assert _opaque_bbox(out.png) == (0, 0, 256, 256)


def test_small_input_does_not_call_thumbnail():
    with mock.patch.object(Image.Image, "thumbnail", autospec=True, side_effect=Image.Image.thumbnail) as spy:
        normalise_logo(_img(32, 32))
    assert not spy.called


def test_small_input_enlarges_before_transpose(monkeypatch):
    order = []
    real_contain, real_tr = ImageOps.contain, ImageOps.exif_transpose
    monkeypatch.setattr(ImageOps, "contain", lambda im, *a, **k: order.append("contain") or real_contain(im, *a, **k))
    monkeypatch.setattr(ImageOps, "exif_transpose", lambda im, *a, **k: order.append("transpose") or real_tr(im, *a, **k))
    out = normalise_logo(_jpeg_with_orientation(6))
    assert order == ["contain", "transpose"]
    left, top, right, bottom = _opaque_bbox(out.png)
    assert (right - left, bottom - top) == (128, 256)


def test_peak_rss_budget_for_max_input(tmp_path):
    for mode in ("RGBA", "LA", "P"):
        kw = {"transparency": 0} if mode == "P" else {}
        (tmp_path / f"{mode}.png").write_bytes(_img(5000, 5000, mode=mode, **kw))
    script = ("import sys; from app.services.account_logo_service import normalise_logo; "
              "normalise_logo(open(sys.argv[1],'rb').read()); "
              "print(next(int(l.split()[1]) for l in open('/proc/self/status') if l.startswith('VmHWM')))")
    for mode in ("RGBA", "LA", "P"):
        out = subprocess.run([sys.executable, "-c", script, str(tmp_path / f"{mode}.png")], capture_output=True,
                             text=True, check=True, env={**os.environ, "PYTHONPATH": "../shared-python-lib:."})
        assert int(out.stdout) * 1024 < PEAK_RSS_BUDGET_BYTES, mode
