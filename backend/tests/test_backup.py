"""Backups, and their second copy off this disk."""

from app.config import get_settings
from app.services.backup import MIRROR_KEEP, make_backup, mirror_latest, mirror_status


def _mirror_to(monkeypatch, path) -> None:
    monkeypatch.setenv("BACKUP_MIRROR_DIR", str(path))
    get_settings.cache_clear()


def test_no_second_place_means_a_warning_not_an_error(client):
    assert mirror_status(get_settings()) == {"dir": None, "reachable": False, "copies": [], "behind": False}
    r = client.post("/api/system/backup").json()
    assert r["mirror"] is None and r["mirror_error"] is None


def test_the_newest_backup_is_copied_once_and_old_copies_go(client, env, monkeypatch):
    stick = env / "usb" / "OwnLifeBackups"  # the drive is there, the folder is created
    stick.parent.mkdir()
    _mirror_to(monkeypatch, stick)
    r = client.post("/api/system/backup").json()
    assert r["mirror"] and (stick / r["path"].split("\\")[-1].split("/")[-1]).exists()
    assert mirror_latest(get_settings()) is None  # already there
    status = client.get("/api/system/backup-mirror").json()
    assert status["reachable"] and not status["behind"] and len(status["copies"]) == 1

    for i in range(MIRROR_KEEP + 2):
        (stick / f"ownlife-2020010{i}-000000-auto.db").write_bytes(b"old")
    make_backup(get_settings(), f"t{i}")
    mirror_latest(get_settings())
    assert len(list(stick.glob("ownlife-*.db"))) == MIRROR_KEEP
    assert not list(stick.glob("*.part"))


def test_an_unplugged_stick_is_tried_again_later(client, env, monkeypatch):
    _mirror_to(monkeypatch, env / "missing-drive" / "OwnLifeBackups")
    client.post("/api/system/backup")
    status = mirror_status(get_settings())
    assert status["reachable"] is False and status["behind"] is True
    assert mirror_latest(get_settings()) is None  # nothing created where the drive should be
    assert not (env / "missing-drive").exists()
