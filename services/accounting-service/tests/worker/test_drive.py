import json

import pytest

from worker import config, drive
from worker.runner import FakeRunner, Result

LISTING = [
    {"Path": "信用卡/國泰世華/2026-09_國泰世華_信用卡.pdf", "Name": "2026-09_國泰世華_信用卡.pdf", "Size": 10,
     "ModTime": "2026-10-01T00:00:00Z", "Hashes": {"md5": "ab" * 16}, "ID": "id-1", "MimeType": "application/pdf"},
    {"Path": "信用卡/渣打銀行/2026-09.PDF", "Name": "2026-09.PDF", "Size": 11, "ModTime": "2026-10-01T00:00:00Z",
     "Hashes": {"md5": "cd" * 16}, "ID": "id-2", "MimeType": "application/pdf"},
    {"Path": "信用卡/星展/notes.txt", "Name": "notes.txt", "Size": 1, "ModTime": "2026-10-01T00:00:00Z",
     "Hashes": {"md5": "ef" * 16}, "ID": "id-3", "MimeType": "text/plain"},
]
MANUAL = [{"Path": "國泰世華/2510.pdf", "Name": "2510.pdf", "Size": 12, "ModTime": "2026-10-09T00:00:00Z",
           "Hashes": {"md5": "01" * 16}, "ID": "id-4", "MimeType": "application/pdf"}]


def _rclone(args, stdin):
    if args[1] == "lsjson":
        body = LISTING if args[-1].endswith("/銀行") else MANUAL
        return Result(0, json.dumps(body).encode(), b"")
    raise AssertionError(args)


def test_list_pdfs_both_roots_case_insensitive(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    items = drive.Drive(cfg, FakeRunner({"rclone": _rclone})).list_pdfs()
    assert [(i.root, i.path, i.drive_file_id) for i in items] == [
        ("mail", "信用卡/國泰世華/2026-09_國泰世華_信用卡.pdf", "id-1"), ("mail", "信用卡/渣打銀行/2026-09.PDF", "id-2"),
        ("manual", "國泰世華/2510.pdf", "id-4")]
    assert drive.folder_of(items[0]) == "信用卡/國泰世華" and drive.folder_of(items[2]) == "國泰世華"
    assert items[0].md5 == "ab" * 16 and items[0].size == 10


def test_listing_error_raises(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    bad = FakeRunner({"rclone": lambda a, s: Result(3, b"", b"directory not found")})
    with pytest.raises(drive.ListingError):
        drive.Drive(cfg, bad).list_pdfs()


def test_download_by_id(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    seen = []

    def rclone(args, stdin):
        seen.append(args)
        (tmp_path / "out.pdf").write_bytes(b"%PDF-1.4 fake")
        return Result(0, b"", b"")

    item = drive.Listed("id-1", "信用卡/國泰世華/x.pdf", "x.pdf", 13, "ab" * 16, "2026-10-01T00:00:00Z", "mail")
    drive.Drive(cfg, FakeRunner({"rclone": rclone})).download(item, tmp_path / "out.pdf")
    assert seen[0][1:4] == ["backend", "copyid", "gdrive:"] and seen[0][4] == "id-1"
