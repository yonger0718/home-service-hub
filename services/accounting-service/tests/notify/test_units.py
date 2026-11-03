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


def _terms(name):
    return (D / f"homehub-notify-terms-{name}.service").read_text()


def test_terms_units_exist_and_pin_cause():
    assert "OnCalendar=*-*-01 09:00:00 Asia/Taipei" in (D / "homehub-notify-terms-monthly.timer").read_text()
    assert "OnCalendar=Mon *-11,12-* 09:00:00 Asia/Taipei" in (D / "homehub-notify-terms-year-end.timer").read_text()
    for cause in ("monthly", "year-end"):
        svc = _terms(cause)
        assert f"-m notify terms --send --cause {cause}" in svc and "Type=oneshot" in svc
        assert "OnFailure=homehub-notify-failed@%n.service" in svc
        assert "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin" in svc.splitlines()
        timer = (D / f"homehub-notify-terms-{cause}.timer").read_text()
        assert "Persistent=true" in timer and f"Unit=homehub-notify-terms-{cause}.service" in timer


def test_terms_services_are_ordered_not_conflicting():
    # Conflicts= would stop an in-flight run when the other slot starts (final review I1); both must run
    for cause in ("monthly", "year-end"):
        assert not [l for l in _terms(cause).splitlines() if l.startswith("Conflicts=")], cause
    assert "After=homehub-notify-terms-monthly.service" in _terms("year-end").splitlines()
    assert not [l for l in _terms("monthly").splitlines() if l.startswith("After=")]


def test_terms_timeout_covers_hermes_timeout():
    for cause in ("monthly", "year-end"):
        (line,) = [l for l in _terms(cause).splitlines() if l.startswith("TimeoutStartSec=")]
        assert int(line.split("=", 1)[1]) >= 1800 + 60


def test_install_does_not_enable_terms_timers():
    lines = (D / "install.sh").read_text().splitlines()
    enables = [l for l in lines if "systemctl" in l and "enable" in l and not l.lstrip().startswith("#")
               and not l.lstrip().startswith("echo")]
    assert enables == ["systemctl --user enable --now homehub-notify-daily.timer homehub-notify-weekly.timer"]
