from pathlib import Path

D = Path(__file__).resolve().parents[2] / "deploy" / "notify"


def test_units_match_spec():
    assert "OnCalendar=*-*-* 07:00:00 Asia/Taipei" in (D / "homehub-notify-daily.timer").read_text()
    assert "OnCalendar=Sat *-*-* 10:00:00 Asia/Taipei" in (D / "homehub-notify-weekly.timer").read_text()
    for name in ("daily", "weekly"):
        svc = (D / f"homehub-notify-{name}.service").read_text()
        assert f"-m notify {name} --send" in svc and "Type=oneshot" in svc and "Environment=TZ=Asia/Taipei" in svc
        assert "OnFailure=homehub-notify-failed@%n.service" in svc
        assert "Persistent=true" in (D / f"homehub-notify-{name}.timer").read_text()
    failed = (D / "homehub-notify-failed@.service").read_text()
    assert "-m notify failed %i" in failed and "OnFailure" not in failed


def test_install_script_lists_every_unit():
    script = (D / "install.sh").read_text()
    for unit in D.glob("homehub-notify-*"):
        assert unit.name in script


def test_every_service_puts_hermes_on_path():
    # `send.py` execs the bare name `hermes` (~/.local/bin); the user manager's default PATH lacks it (Ruling 16)
    for name in ("homehub-notify-daily.service", "homehub-notify-weekly.service", "homehub-notify-failed@.service"):
        lines = (D / name).read_text().splitlines()
        assert "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin" in lines, name
