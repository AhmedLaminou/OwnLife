"""The built-in window tracker: which program and window you are using, read
from Windows itself — nothing to install, nothing leaves this machine.

    every 5 s     the foreground window: its program (Code.exe) and title,
                  only while you use the computer — after 2 minutes without
                  keyboard or mouse nothing is counted
    → category    by the same rules as everything else (program, title)
    → blocks      samples of one category with gaps under 2 minutes form a
                  block; written to the ledger once it reaches a minute, then
                  extended every minute while it lasts

Its blocks rank below your entries, the timer and the YouTube extension: they
fill the time nothing better describes. Titles of private and incognito
windows are never kept. Reading or watching without touching anything for 2
minutes is not counted (YouTube playback is measured by the extension).
Browser tabs are classified by their title: Windows does not tell the address.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import sys
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import SessionLocal, utcnow
from app.models import Profile, TimeEntry, User
from app.services.rules import Activity, RuleSet, load_ruleset

log = logging.getLogger("ownlife")

SOURCE = "window"
SAMPLE = timedelta(seconds=5)
IDLE_SECONDS = 120
MERGE_GAP = timedelta(minutes=2)
MIN_BLOCK = timedelta(minutes=1)
FLUSH = timedelta(minutes=1)
RULES_TTL = timedelta(minutes=5)
DEFAULTS = {"enabled": True}
PRIVATE = re.compile(r"InPrivate|Incognito|Navigation privée|Private Browsing", re.I)
# Windows' own surfaces: the lock screen, the start menu, search, the task switcher.
IGNORED = {"LockApp.exe", "StartMenuExperienceHost.exe", "SearchHost.exe", "ShellExperienceHost.exe",
           "SearchApp.exe", "ScreenClippingHost.exe", ""}


def settings_with_defaults(prefs: dict | None) -> dict:
    return {**DEFAULTS, **(prefs or {})}


def available() -> bool:
    return sys.platform == "win32"


# ---------------------------------------------------------------- Windows
@dataclass
class Sample:
    app: str  # "Code.exe"
    title: str


_api = None


def _windows():
    """user32 and kernel32 with their signatures declared: handles are 64-bit."""
    global _api
    if _api is None:
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetTickCount.restype = wintypes.DWORD
        _api = (user32, kernel32, LASTINPUTINFO, wintypes)
    return _api


def foreground() -> Sample | None:
    """The program and title of the window in front, or None (no window, the desktop)."""
    user32, kernel32, _, wintypes = _windows()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    text = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, text, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if handle:
        try:
            size = wintypes.DWORD(1024)
            path = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                app = os.path.basename(path.value)
        finally:
            kernel32.CloseHandle(handle)
    return Sample(app, text.value)


def idle_seconds() -> float:
    """Seconds since the last keyboard or mouse input (any program)."""
    user32, kernel32, info_type, _ = _windows()
    info = info_type(cbSize=ctypes.sizeof(info_type))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0  # the tick counter wraps


# ---------------------------------------------------------------- samples → blocks
@dataclass
class Block:
    category_id: int | None
    start: datetime
    end: datetime
    apps: Counter = field(default_factory=Counter)
    titles: Counter = field(default_factory=Counter)
    entry_id: int | None = None  # once written; -1 when you deleted it meanwhile


def app_name(exe: str) -> str:
    """'Code.exe' -> 'Code', 'WINWORD.EXE' -> 'WINWORD'."""
    return exe[:-4] if exe.lower().endswith(".exe") else exe


def _label(b: Block) -> str:
    title = b.titles.most_common(1)[0][0] if b.titles else ""
    app = app_name(b.apps.most_common(1)[0][0]) if b.apps else "Computer"
    if not title or title == "(private window)":
        return f"{app} · private window" if title else app
    return title[:120]


class Tracker:
    """The sampler. `step` takes one sample; the thread calls it every 5 s
    (the tests call it with a fake window and clock)."""

    def __init__(self, settings: Settings, probe: Callable[[], Sample | None] = foreground,
                 idle: Callable[[], float] = idle_seconds) -> None:
        self.settings = settings
        self.probe = probe
        self.idle = idle
        self.block: Block | None = None
        self.last_active: datetime | None = None
        self.last_flush: datetime | None = None
        self.last_sample: dict | None = None
        self._rules: RuleSet | None = None
        self._rules_at: datetime | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    def _classify(self, db: Session, user_id: int, s: Sample, now: datetime) -> int | None:
        if self._rules is None or self._rules_at is None or now - self._rules_at > RULES_TTL:
            self._rules, self._rules_at = load_ruleset(db, user_id), now
        return self._rules.classify(Activity(title=s.title, app=s.app))

    def step(self, db: Session, user_id: int, now: datetime) -> None:
        idle = self.idle()
        sample = self.probe() if idle < IDLE_SECONDS else None
        if sample is None or sample.app in IGNORED:
            self.last_sample = None
            if self.block is not None and now - self.block.end > MERGE_GAP:
                self._close(db, user_id)
            return
        if PRIVATE.search(sample.title):
            sample = Sample(sample.app, "(private window)")
        self.last_sample = {"app": sample.app, "at": now.isoformat()}
        category = self._classify(db, user_id, sample, now)
        b = self.block
        if b is not None and b.category_id == category and now - b.end <= MERGE_GAP:
            b.end = now
        else:
            self._close(db, user_id)
            continuous = self.last_active is not None and now - self.last_active <= SAMPLE * 2
            b = self.block = Block(category, self.last_active if continuous else now - SAMPLE, now)
        b.apps[sample.app] += SAMPLE.total_seconds()
        b.titles[sample.title] += SAMPLE.total_seconds()
        self.last_active = now
        if self.last_flush is None or now - self.last_flush >= FLUSH:
            self.flush(db, user_id)
            self.last_flush = now

    def flush(self, db: Session, user_id: int) -> None:
        """Writes the open block as it stands (and keeps it open)."""
        if self.block is not None:
            self._write(db, user_id, self.block)

    def _close(self, db: Session, user_id: int) -> None:
        if self.block is not None:
            self._write(db, user_id, self.block)
        self.block = None

    def _write(self, db: Session, user_id: int, b: Block) -> None:
        if b.entry_id == -1 or (b.entry_id is None and b.end - b.start < MIN_BLOCK):
            return
        meta = {"apps": {k: round(v) for k, v in b.apps.most_common(5)},
                "titles": [t for t, _ in b.titles.most_common(5)]}
        if b.entry_id is not None:
            e = db.get(TimeEntry, b.entry_id)
            if e is None:  # deleted from the ledger while it was still open: leave it deleted
                b.entry_id = -1
                return
            e.ended_at = b.end
            e.meta = meta
            if not e.category_locked:
                e.title = _label(b)
            db.commit()
            return
        e = TimeEntry(user_id=user_id, title=_label(b), category_id=b.category_id, started_at=b.start,
                      ended_at=b.end, source=SOURCE, source_ref=f"win:{b.start.isoformat()}", meta=meta)
        db.add(e)
        db.commit()
        b.entry_id = e.id

    # ---------------------------------------------------------- the thread
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="window-tracker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            with SessionLocal() as db:
                owner = _owner(db)
                if owner is not None:
                    self._close(db, owner.id)
        except Exception:  # closing down: losing the last minute is acceptable
            pass

    def _loop(self) -> None:
        enabled_at: datetime | None = None
        enabled = True
        while not self._stop.wait(SAMPLE.total_seconds()):
            now = utcnow()
            try:
                with SessionLocal() as db:
                    owner = _owner(db)
                    if owner is None:
                        continue
                    if enabled_at is None or now - enabled_at > FLUSH:
                        profile = db.get(Profile, owner.id)
                        enabled = settings_with_defaults((profile.prefs or {}).get("window_tracker"))["enabled"]
                        enabled_at = now
                    if not enabled:
                        self._close(db, owner.id)
                        self.last_sample = None
                        continue
                    self.step(db, owner.id, now)
                    self.error = None
            except Exception as e:
                if self.error is None:
                    log.exception("Window tracker")
                self.error = f"{e.__class__.__name__}: {str(e)[:160]}"

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def _owner(db: Session) -> User | None:
    from app.services.filesync import sync_owner

    return sync_owner(db)


_tracker: Tracker | None = None


def start(settings: Settings) -> Tracker | None:
    global _tracker
    if not available():
        return None
    _tracker = Tracker(settings)
    _tracker.start()
    return _tracker


def stop() -> None:
    global _tracker
    if _tracker is not None:
        _tracker.stop()
    _tracker = None


def status(db: Session, user_id: int, profile: Profile, day_start: datetime, now: datetime) -> dict:
    seconds = 0.0
    for started, ended in db.execute(select(TimeEntry.started_at, TimeEntry.ended_at).where(
            TimeEntry.user_id == user_id, TimeEntry.source == SOURCE, TimeEntry.ended_at > day_start)):
        seconds += ((ended or now) - max(started, day_start)).total_seconds()
    blocks = db.scalar(select(func.count()).select_from(TimeEntry).where(
        TimeEntry.user_id == user_id, TimeEntry.source == SOURCE, TimeEntry.ended_at > day_start)) or 0
    t = _tracker
    return {
        "available": available(),
        "enabled": settings_with_defaults((profile.prefs or {}).get("window_tracker"))["enabled"],
        "running": bool(t and t.running()),
        "last": t.last_sample if t else None,
        "error": t.error if t else None,
        "today_seconds": round(seconds),
        "today_blocks": blocks,
    }
