import json

import pytest

from worker import config, drive
from worker.runner import FakeRunner, Result, SubprocessRunner

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


def _roots(mail: Result, manual: Result):
    def handler(args, stdin):
        return mail if args[-1].endswith("/銀行") else manual
    return FakeRunner({"rclone": handler})


OK = Result(0, b"[]", b"")


def test_list_pdfs_both_roots_case_insensitive(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    items = drive.Drive(cfg, FakeRunner({"rclone": _rclone})).list_pdfs()
    assert [(i.root, i.path, i.drive_file_id) for i in items] == [
        ("mail", "信用卡/國泰世華/2026-09_國泰世華_信用卡.pdf", "id-1"), ("mail", "信用卡/渣打銀行/2026-09.PDF", "id-2"),
        ("manual", "國泰世華/2510.pdf", "id-4")]
    assert drive.folder_of(items[0]) == "信用卡/國泰世華" and drive.folder_of(items[2]) == "國泰世華"
    assert items[0].md5 == "ab" * 16 and items[0].size == 10


def test_lsjson_argv_flags_and_remote(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    runner = FakeRunner({"rclone": _rclone})
    drive.Drive(cfg, runner).list_pdfs()
    assert runner.calls[0] == ["rclone", "lsjson", "--hash", "--recursive", "--files-only",
                               "gdrive:財務對帳單/銀行"]
    assert runner.calls[1] == ["rclone", "lsjson", "--hash", "--recursive", "--files-only",
                               "gdrive:財務對帳單/手動下載"]


def test_upper_case_md5_is_lowercased(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    row = [{"Path": "a.pdf", "Name": "a.pdf", "Size": 1, "ModTime": "", "Hashes": {"md5": "AB" * 16}, "ID": "x"}]
    items = drive.Drive(cfg, _roots(Result(0, json.dumps(row).encode(), b""), OK)).list_pdfs()
    assert items[0].md5 == "ab" * 16


def test_listing_error_raises(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    bad = FakeRunner({"rclone": lambda a, s: Result(3, b"", b"directory not found")})
    with pytest.raises(drive.ListingError):
        drive.Drive(cfg, bad).list_pdfs()


@pytest.mark.parametrize("stdout", [
    b"not json",
    b'{"Path": "a.pdf"}',
    b'[{"Path": "a.pdf", "Name": "a.pdf", "Size": 1, "ID": "x", "Hashes": {}}]',
    b'[{"Path": "a.pdf", "Name": "a.pdf", "Size": 1, "Hashes": {"md5": "ab"}}]',
])
def test_listing_error_on_bad_output(tmp_path, stdout):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    with pytest.raises(drive.ListingError):
        drive.Drive(cfg, _roots(Result(0, stdout, b""), OK)).list_pdfs()


def test_second_root_failure_is_a_listing_error(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    with pytest.raises(drive.ListingError):
        drive.Drive(cfg, _roots(Result(0, b"[]", b""), Result(1, b"", b"boom"))).list_pdfs()


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
    assert seen[0][5] == str(tmp_path / "out.pdf")
    assert oct((tmp_path / "out.pdf").stat().st_mode)[-3:] == "600"


def test_download_nonzero_exit_raises(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    item = drive.Listed("id-1", "x.pdf", "x.pdf", 1, "ab" * 16, "", "mail")
    runner = FakeRunner({"rclone": lambda a, s: Result(1, b"", b"quota")})
    with pytest.raises(drive.DownloadError):
        drive.Drive(cfg, runner).download(item, tmp_path / "out.pdf")


def test_download_success_without_file_raises(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    item = drive.Listed("id-1", "x.pdf", "x.pdf", 1, "ab" * 16, "", "mail")
    runner = FakeRunner({"rclone": lambda a, s: Result(0, b"", b"")})
    with pytest.raises(drive.DownloadError):
        drive.Drive(cfg, runner).download(item, tmp_path / "out.pdf")


def test_download_removes_stale_dest_first(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    dest = tmp_path / "out.pdf"
    dest.write_bytes(b"stale")
    existed = []

    def rclone(args, stdin):
        existed.append(dest.exists())
        return Result(1, b"", b"fail")

    item = drive.Listed("id-1", "x.pdf", "x.pdf", 1, "ab" * 16, "", "mail")
    with pytest.raises(drive.DownloadError):
        drive.Drive(cfg, FakeRunner({"rclone": rclone})).download(item, dest)
    assert existed == [False]


def test_subprocess_runner_missing_binary_maps_to_127():
    result = SubprocessRunner().run(["home-hub-no-such-binary-xyz"])
    assert result.returncode == 127 and result.stdout == b""
