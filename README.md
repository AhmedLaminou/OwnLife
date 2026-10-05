# OwnLife

A local-first record of one life: where the hours go, what was written, what is
planned, and where the current habits lead — on your machine, in your database.

It turns a Markdown journal (the "Virtual Memory") and the arithmetic of a life
in weeks into a working system:

| Page | What it does |
| --- | --- |
| **Today** | Core work vs. your daily target, noise vs. its budget, the day hour by hour, timer, habits, the next prayer, milestones |
| **Life** | 90 years in weeks, chapters past and planned, dated moments, waking hours left, where the last 30 days lead by 60, "what if" |
| **Ledger** | Every logged block — timer (with pause), the built-in window tracker, the YouTube extension, blocks read from your journal — day timeline and range statistics, by kind; no minute counted twice when two sources saw it |
| **Planner** | The plan next to what happened, adherence, day templates whose prayers follow the real prayer times |
| **Journal** | The Virtual Memory, synced both ways with your `.md` files as you write; searchable by words *and* meaning; notes and essays |
| **Assistant** | Chat with your record (LangGraph agent with 21 tools + RAG): it reads the ledger, YouTube, money, habits, prayer times and life events, logs and plans; capture a day in free text, undo what it did, a review of each day |
| **Goals** | [BigVision] → objectives → milestones, hours invested from the ledger |
| **Habits** | Built habits (some evaluated from the data itself), quit habits with clean days, urges and relapse patterns |
| **Watching** | YouTube minutes measured live by the OwnLife extension — new videos sorted by the local model, the rest counted as noise until you sort them; a notification and a red badge when the day's noise budget is spent; the past from Google Takeout; books and courses |
| **Money / People** | FCFA in and out, the amounts written in your journal found and proposed by themselves; who the time is spent with |

## Start

First time (installs everything, builds the frontend, checks the machine):

```powershell
cd OwnLife
pwsh -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

Every day: double-click **`OwnLife.cmd`**, or

```powershell
cd OwnLife\backend
.\.venv\Scripts\python.exe -m app            # → http://127.0.0.1:8000
```

So that the evening reminders fire and the files stay in sync without opening
anything, let it start with Windows (one shortcut in your Startup folder, no window;
`-Off` removes it):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\autostart.ps1
```

The first visit asks you to create your account. Then **Settings → Data →
Personal seed → Apply** adds your chapters, moments, goals, habits, people, rules
and templates. The journal and the notes beside it arrive by themselves: OwnLife
watches the files.

Development (auto-reload backend + hot-reload frontend): `pwsh -File .\scripts\dev.ps1`,
then <http://localhost:5173>. Careful: the dev backend runs on your real data, and
reloads on every code change.

> Always run Python tools as `python -m …`. Smart App Control blocks the `.exe`
> shims that pip generates (`uvicorn.exe`, `pytest.exe`).

## Connect the real services

AI, the YouTube extension, ActivityWatch and YouTube history each need a step
from you — **[docs/GOING_LIVE.md](docs/GOING_LIVE.md)** lists exactly what to do.
Everything else works out of the box, offline included.

## Commands

From `backend/`:

| Command | |
| --- | --- |
| `.\.venv\Scripts\python.exe -m app` | run the app |
| `.\.venv\Scripts\pythonw.exe -m app --background` | run it without a window (what the autostart shortcut does) |
| `.\.venv\Scripts\python.exe -m app.doctor --online` | check Python, Smart App Control, Ollama, OpenRouter, ActivityWatch |
| `.\.venv\Scripts\python.exe -m app.cli seed-personal` | apply `data/personal_seed.json` (same as the Settings button) |
| `.\.venv\Scripts\python.exe -m app.cli sync` | a two-way sync with the files, now (the app does it live) |
| `.\.venv\Scripts\python.exe -m app.cli reindex` | rebuild the search index with progress |
| `.\.venv\Scripts\python.exe -m app.cli backup` | backup now (also automatic daily, and before every database upgrade) |
| `.\.venv\Scripts\python.exe -m app.cli reset-password` | forgotten password (local only) |
| `.\.venv\Scripts\python.exe -m pytest tests -q` | 130 backend tests |

From `frontend/`: `npm run dev`, `npm run build`, `npm test`, `npm run typecheck`.

## Where things are

```text
OwnLife/
  OwnLife.cmd            double-click to start
  backend/
    app/                 FastAPI app — models, services, routers, ai/
    alembic/             database migrations
    tests/               pytest suite (throwaway DB, account and files per test)
    data/                YOUR DATA — ownlife.db, backups/, file-history/, logs/, personal_seed.json (git-ignored)
    .env                 your settings and keys (git-ignored)
  frontend/src/          React + TypeScript + Tailwind + Motion
  extension/             the OwnLife YouTube extension (Chrome / Edge, load unpacked)
  docs/                  GOING_LIVE · ARCHITECTURE · ROADMAP
  scripts/               setup · start · dev · autostart · make_icons
```

## Privacy, in one paragraph

Everything is stored in `backend/data/ownlife.db` on this laptop, and your
Markdown files stay the master copy of your writing. Search and embeddings run
locally (Ollama); the YouTube extension and the notifications talk only to this
machine. Only when you ask the assistant something (or when the morning review
is written) does text leave the machine: your message, the day's numbers and the
few passages relevant to the question — after your redactions — and never pages
or entries you marked private. Local AI mode keeps even that on the machine.
`backend/data/` and `.env` are git-ignored: pushing the code to GitHub never
pushes your life.
