"""Application factory.

    python -m app                      # run (serves the built frontend too)
    python -m uvicorn app.main:create_app --factory --reload   # development

Run tools as `python -m …`: Smart App Control blocks the pip-generated .exe shims.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.config import PROJECT_DIR, Settings, get_settings
from app.db import SessionLocal, init_engine
from app.migrate import pending_upgrade, upgrade_database
from app.models import IntegrationState, User
from app.routers import (
    ai,
    auth,
    categories,
    devices,
    goals,
    habits,
    integrations,
    journal,
    ledger,
    life,
    media,
    money,
    people,
    plan,
    prefs,
    profile,
    system,
)
from app.security import CSRF_HEADER
from app.services import filesync, journal_money, reminders
from app.services.activitywatch import ActivityWatchError
from app.services.backup import backup_if_due, make_backup

log = logging.getLogger("ownlife")

_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


async def _periodic_backup(settings: Settings) -> None:
    while True:
        try:
            path = await run_in_threadpool(backup_if_due, settings)
            if path:
                log.info("Automatic backup: %s", path)
        except Exception:
            log.exception("Automatic backup failed")
        await asyncio.sleep(6 * 3600)


async def _periodic_reminders(settings: Settings) -> None:
    await asyncio.sleep(15)
    while True:
        try:
            await run_in_threadpool(reminders.tick, settings)
        except Exception:
            log.exception("Reminders failed")
        await asyncio.sleep(60)


async def _periodic_journal_money(settings: Settings) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await run_in_threadpool(journal_money.tick, settings)
        except Exception:
            log.exception("Reading money from the journal failed")
        await asyncio.sleep(300)


async def _periodic_activitywatch(settings: Settings) -> None:
    while True:
        await asyncio.sleep(settings.activitywatch_autosync_minutes * 60)
        with SessionLocal() as db:
            user_ids = list(db.scalars(select(User.id).where(User.is_active.is_(True))))
            # Only accounts that ran one sync by hand (opening the settings page is not consent).
            enabled = {
                s.user_id
                for s in db.scalars(select(IntegrationState).where(
                    IntegrationState.provider == "activitywatch", IntegrationState.last_synced_at.is_not(None)))
            }
        for uid in user_ids:
            if uid not in enabled:
                continue  # only users who synced at least once by hand
            try:
                await run_in_threadpool(integrations.run_sync, uid)
            except ActivityWatchError:
                pass  # ActivityWatch not running: try again next time
            except Exception:
                log.exception("ActivityWatch sync failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    url = settings.effective_database_url
    if pending_upgrade(url):
        # The schema is about to change: keep a copy of the database as it was.
        try:
            log.info("Backup before migrating the database: %s", make_backup(settings, "pre-migration"))
        except RuntimeError as e:  # not SQLite: back it up with the database's own tools
            log.warning("No automatic backup before migrating: %s", e)
    upgrade_database(url)
    init_engine(url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks = []
        watcher = None
        if settings.environment != "test":
            tasks.append(asyncio.create_task(_periodic_backup(settings)))
            if settings.activitywatch_autosync_minutes > 0:
                tasks.append(asyncio.create_task(_periodic_activitywatch(settings)))
            tasks.append(asyncio.create_task(_periodic_reminders(settings)))
            tasks.append(asyncio.create_task(_periodic_journal_money(settings)))
            if settings.file_sync and filesync.config(settings) is not None:
                watcher = filesync.Watcher(settings)
                filesync.set_watcher(watcher)
                watcher.start()
                log.info("Watching %s (and the notes beside it)", settings.journal_sync_path)
        yield
        if watcher is not None:
            watcher.stop()
            filesync.set_watcher(None)
        for t in tasks:
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t

    dev = settings.environment != "production"
    app = FastAPI(
        title="OwnLife",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if dev else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if dev else None,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and request.method in _UNSAFE and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "Missing X-OwnLife header"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if not path.startswith("/api/"):
            response.headers.setdefault("Content-Security-Policy", _CSP)
        return response

    for r in (auth, profile, prefs, categories, ledger, journal, goals, habits, plan, media, money, people, life,
              ai, integrations, devices, system):
        app.include_router(r.router)

    dist = PROJECT_DIR / "frontend" / "dist"
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse(
                {
                    "app": "OwnLife API",
                    "version": __version__,
                    "hint": "The frontend is not built. Run `npm run build` in frontend/, or use `npm run dev`.",
                    "docs": "/api/docs",
                }
            )

    return app
