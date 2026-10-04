"""Command line: `python -m app.cli <command>` (run from backend/).

    seed-personal [path]      apply backend/data/personal_seed.json to your account
    import-journal <path>     import a Virtual Memory .md file
    sync                      two-way sync with the journal file(s) and notes (the app also does it live)
    reindex                   rebuild the search index and embed everything (shows progress)
    backup                    make a database backup now
    reset-password            set a new password (asks for it; local recovery only)
    doctor                    check every dependency and service
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import DATA_DIR, get_settings


def _boot():
    from app.db import SessionLocal, init_engine
    from app.migrate import upgrade_database

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade_database(s.effective_database_url)
    init_engine(s.effective_database_url)
    return SessionLocal


def _user(db, email: str | None):
    from app.models import User

    users = db.scalars(select(User)).all()
    if not users:
        sys.exit("No account yet: open the app and create it first.")
    if email:
        match = [u for u in users if u.email == email.lower()]
        if not match:
            sys.exit(f"No account with email {email}")
        return match[0]
    if len(users) > 1:
        sys.exit("Several accounts exist: pass --email")
    return users[0]


def cmd_seed(args) -> None:
    from app.seed.personal import apply_personal_seed, load_seed

    path = Path(args.path) if args.path else DATA_DIR / "personal_seed.json"
    if not path.exists():
        sys.exit(f"{path} not found")
    Session = _boot()
    with Session() as db:
        user = _user(db, args.email)
        report = apply_personal_seed(db, user, load_seed(path))
        db.commit()
    print("Applied:", ", ".join(f"{k}={v}" for k, v in report.items()) or "nothing new")


def cmd_import(args) -> None:
    from app.ai import rag
    from app.models import JournalEntry, Profile
    from app.services import filesync
    from app.services.journal_import import import_virtual_memory

    if filesync.config(get_settings()) is not None and not args.force:
        sys.exit("Your journal file is synced live (JOURNAL_SYNC_PATH): use `sync` instead. "
                 "A one-off import could overwrite newer text. Add --force to import anyway.")
    Session = _boot()
    text = Path(args.path).read_text(encoding="utf-8", errors="replace")
    with Session() as db:
        user = _user(db, args.email)
        report = import_virtual_memory(db, user.id, db.get(Profile, user.id), text)
        for eid in report.changed_entry_ids:
            rag.index_journal_entry(db, db.get(JournalEntry, eid))
        db.commit()
    print(report.as_dict())
    print("Run `python -m app.cli reindex` to compute the embeddings now (the app also does it in the background).")


def cmd_sync(args) -> None:
    """The same two-way sync the running app does by itself."""
    from app.services import filesync

    cfg = filesync.config(get_settings())
    if cfg is None:
        sys.exit("Set JOURNAL_SYNC_PATH and IMPORT_ROOT in backend/.env")
    Session = _boot()
    with Session() as db:
        user = _user(db, args.email)
        report = filesync.sync_all(db, user, cfg)
        filesync.reindex(db, report)
        db.commit()
    print({k: v for k, v in report.as_dict().items() if v and k != "changed_entry_ids"} or "Everything already in sync.")


def cmd_reindex(args) -> None:
    from app.ai import rag
    from app.ai.embeddings import get_embedder

    Session = _boot()
    with Session() as db:
        user = _user(db, args.email)
        n = rag.reindex_all(db, user.id)
        db.commit()
        print(f"{n} chunks indexed for keyword search.")
        embedder = get_embedder(get_settings())
        if embedder is None:
            print("Embeddings disabled (EMBEDDINGS_PROVIDER=none): keyword search only.")
            return
        ok, msg = embedder.available()
        if not ok:
            print(f"Embeddings skipped: {msg}. Keyword search works; run this again when Ollama is up.")
            return
        done = rag.embed_pending(db, user.id, embedder,
                                 on_progress=lambda d, t: print(f"\r  embedded {d}/{t}", end="", flush=True))
        print(f"\n{done} chunks embedded with {embedder.name}.")


def cmd_backup(_args) -> None:
    from app.services.backup import make_backup

    _boot()
    print(make_backup(get_settings(), "manual"))


def cmd_reset_password(args) -> None:
    from app.models import AuthSession, User
    from app.security import hash_password

    Session = _boot()
    with Session() as db:
        user: User = _user(db, args.email)
        first = getpass.getpass(f"New password for {user.email}: ")
        if len(first) < 10:
            sys.exit("At least 10 characters.")
        if getpass.getpass("Again: ") != first:
            sys.exit("The two entries differ.")
        user.password_hash = hash_password(first)
        for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
            db.delete(s)
        db.commit()
    print("Password changed; every session was logged out.")


def cmd_doctor(args) -> None:
    from app.doctor import run

    sys.exit(run(online=args.online))


def main(argv: list[str] | None = None) -> None:
    from app.doctor import utf8_console

    utf8_console()
    p = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", help="account to act on (when several exist)")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("seed-personal")
    s.add_argument("path", nargs="?")
    s.set_defaults(func=cmd_seed)
    s = sub.add_parser("import-journal")
    s.add_argument("path")
    s.add_argument("--force", action="store_true", help="import even though the journal file is synced")
    s.set_defaults(func=cmd_import)
    sub.add_parser("sync").set_defaults(func=cmd_sync)
    sub.add_parser("reindex").set_defaults(func=cmd_reindex)
    sub.add_parser("backup").set_defaults(func=cmd_backup)
    sub.add_parser("reset-password").set_defaults(func=cmd_reset_password)
    s = sub.add_parser("doctor")
    s.add_argument("--online", action="store_true", help="also check OpenRouter (uses no model request)")
    s.set_defaults(func=cmd_doctor)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
