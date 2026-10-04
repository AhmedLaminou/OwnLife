# How OwnLife works

Written so that every part can be understood, changed and defended — not just run.

## The stack, and why

| Layer | Choice | Why |
| --- | --- | --- |
| Backend | Python 3.11, **FastAPI**, Pydantic v2 | LangGraph and LangChain are Python-first; you already work in Python 3.11 |
| Database | **SQLite** (WAL, `synchronous=FULL`) via **SQLAlchemy 2** + **Alembic** | see below |
| Search | SQLite **FTS5** (BM25) + local embeddings (Ollama `embeddinggemma`), fused with RRF | hybrid: exact names *and* meaning, all offline |
| AI | **LangGraph** graph, **LangChain** `ChatOpenAI` → **OpenRouter** free models, **Ollama** fallback | free, and keeps working during internet cuts |
| Frontend | **React 19**, TypeScript, Vite 8, **Tailwind 4**, **Motion**, TanStack Query, React Router 7 | the stack of your portfolio |
| Auth | argon2 (pwdlib), opaque session token in an httpOnly cookie, CSRF header, login throttle | simple, revocable, safe for one machine or many users |

### SQLite or MySQL?

**SQLite, for now — and PostgreSQL, not MySQL, if OwnLife ever becomes a SaaS.**

- SQLite is a file: no server to start after every blackout reboot, no password to
  manage, no RAM to give away. A backup is a file copy (done daily, with the online
  backup API so it is consistent even mid-write).
- One person writes a few hundred rows a day; SQLite handles millions. MySQL's
  strengths (many concurrent writers, replication) solve problems this app does
  not have.
- FTS5 full-text search is built in. With WAL + `synchronous=FULL`, a committed
  entry survives a crash or a power cut.
- For a multi-user SaaS, **PostgreSQL** beats MySQL here: `pgvector` puts the
  embeddings in the database, JSONB, row-level security for tenants, better
  full-text search. Every table already has a `user_id`, and SQLAlchemy +
  Alembic make the switch mostly a `DATABASE_URL` change — the two SQLite-specific
  pieces (FTS5, the vector cache) sit behind `app/ai/rag.py`.

## Backend map

```text
app/
  main.py            app factory: migrations at startup, CSRF guard, security headers,
                     serves frontend/dist, daily backup + ActivityWatch tasks
  config.py          settings from backend/.env
  db.py              engine, sessions, UTCDateTime (store UTC, return aware datetimes)
  models/            20 tables: core (users, sessions, profile), ledger (categories,
                     time entries, people, rules), memory (journal, notes, chunks),
                     direction (goals, chapters, habits, plans), consumption (media,
                     YouTube events, money), assistant (chat, drafts, integrations)
  services/          the logic, no HTTP: ledger maths (and which source wins),
                     life projections, habits, goals, journal parsing, the two-way
                     file sync (mdfile, filesync), YouTube history and live minutes
                     (youtube, watchlive), ActivityWatch, rules, quick-log, records,
                     undo, prayer times, reminders and notifications, backups,
                     privacy, indexer
  ai/                llm (models + fallbacks), embeddings, rag, prompts, tools,
                     agent (the graph), capture, review
  routers/           thin HTTP layer: validate input, call services, serialise
  seed/              defaults for every account; personal.py applies your seed file
  cli.py, doctor.py  command line tools
```

## Time: the one subtle thing

Instants are stored in **UTC**; days are **your local days** (`Africa/Niamey`,
UTC+1). "What did I do on 12/09" means local midnight to midnight. An entry from
23:10 to 01:40 is split between two days in every statistic
(`services/ledger.split_by_day`). Overlapping entries (Quran on the way to work)
are not double-counted in coverage (`covered_seconds` takes the union).

## Signal vs noise: kinds

Every category has a **kind**: `core` (the mission), `growth`, `work`, `spirit`,
`social`, `body`, `maintenance`, `noise`, `destructive`. All charts and averages
run on kinds, so renaming a category never breaks a statistic. `destructive` is
folded into `noise` in every chart — eight colours is the limit of a palette that
stays readable for colour-blind eyes, and folding it in is also the discreet choice.

## Habits

- **Build** habits are ticked by hand, *or* evaluated from data through a rule:
  `kind_hours_min` ("minimum day: 4h of core"), `kind_hours_max` ("noise ≤ 1h"),
  `journal_written`. A rule-based habit cannot be ticked by wishful thinking.
- **Quit** habits count clean days since the last relapse; urges resisted are logged
  too. The relapse hour-of-day histogram is the pattern worth watching.

## The life projections

`services/life.py`: averages per kind over the last 30 *tracked* days (≥ 2h logged
— a forgotten day is not counted as a day of zero study), carried to age 60 and to
the end of the horizon, expressed in "continuous years" (hours ÷ 8,766). The
"what if" moves noise hours into core hours.

## Reading the journal

`services/journal_import.py` reads the Virtual Memory format: glossary lines in the
preamble (`{X} = …`, terms in braces become private), one
`Day N : DD/MM/YYYY` header per day (tolerates a leading space, `27/009/2026` and
`13-09-2026`), tags (`[SomeThought]`/`[SomeTHoughts]` folded into `[SomeThoughts]`).
It is built on the same line model as the writer (`mdfile.py`), so what is read
and what is written can never disagree. A header with an impossible date stays
text of the day before, with a warning — no text vanishes because of a typo.

## Your files, both ways (`services/mdfile.py`, `services/filesync.py`)

The Markdown files stay the master copy of the writing; the database is a mirror
that can be searched, linked and analysed.

- **Watching.** A thread `stat`s the journal file(s) and the `.md` files beside
  them every second; after a change has been quiet for 0.8 s it runs a full sync.
  Polling, not OS file events: editors save in-place, by rename or through temp
  files, and a stat loop sees all of them the same. A full sync of 34 days and 8
  notes takes a few tens of milliseconds.
- **Three-way comparison.** Each day and note remembers the text both sides last
  agreed on — its *base* (`source_hash`). File = base and app = base: nothing to
  do. Only the file changed: take it. Only OwnLife changed: write it. Both
  changed differently: a **conflict** — both versions are kept (`conflict_body`)
  and the person chooses or merges; nothing is overwritten.
- **Writing.** A day's text is replaced by a line-level diff (`difflib`) against
  the current file, so unchanged lines are reused byte for byte — CRLF endings,
  the trailing spaces of Markdown line breaks, the missing final newline, a BOM.
  The new text is parsed again before it is written: the edited day must read back
  as intended and every other day, and the preamble, must be identical; a line
  that looks like a day header inside a day is refused. The previous file goes to
  `data/file-history/`, then the new one is written to a temporary file and
  renamed over the original (atomic on Windows and Linux).
- **Never a lost update.** An editor sends the hash of the version it opened; a
  save based on an older version is refused (409) instead of overwriting text
  saved in VS Code meanwhile. One lock serialises every read-modify-write.
- **Nothing silently disappears.** A day whose header vanishes is marked
  *missing* (hidden from lists, search and AI); when it comes back it is restored
  with its private flag and metadata. A day whose date was corrected is
  recognised by its text and moved, not duplicated. A file that is not valid
  UTF-8 is never written. Nothing outside `IMPORT_ROOT` is read or written.
- `JOURNAL_SYNC_PATH` may contain `{year}`: every year's file is synced, and a new
  page goes to its year's file (created if needed).

Checked on a copy of the real journal (34 days, 110 kB, CRLF, 396 lines with
trailing spaces): parsing matches the previous importer exactly, rewriting every
day with its own text gives back identical bytes, and a one-line edit changes
one line.

## When two sources saw the same minutes (`services/ledger.effective_spans`)

The order: entries you typed (form, quick-log, capture, assistant), then the
YouTube extension's measured minutes, then **the timer**, then ActivityWatch, then
history estimates last. The timer sits below measured YouTube because a timer
only knows when something started: if you switch to a video without pausing, the
measured minutes of that video count, not the timer's. Measured YouTube *without*
a category ranks below the timer, so a lecture from a channel that has no rule
yet cannot erase study time. Every statistic runs on *effective spans*: an
entry's time minus what higher-ranked entries already cover (a sweep over merged
intervals, O(n log n)). Entries of the same rank all count — reciting Quran
during the commute is two true things. When an automatic record and one of your
entries *of another kind* share 10 minutes or more ("YouTube during Sleep",
"reaction videos during the Linear algebra timer"), the day view says so, and
which one the totals used.

## Timer pauses (`services/records.py`)

Pause stops the running segment and marks it `paused`; Resume starts a new
segment with the same title, category, people and notes, and both carry the same
`session` (the first segment's id). The pause is simply time no segment covers —
free for whatever happened instead. Several timers can be paused at once; a pause
older than 18 hours counts as finished. The timer shows the whole session's time.
Starting, pausing and stopping all take "minutes ago" (forgetting the button is
normal; a timer started 30 minutes ago stops the one that was running at that
moment, not now), and a timer still running after 2 hours (setting) gets a
"still on it?" notification, then again at 4 and 6 hours, never more.

## Money from the journal (`services/journal_money.py`)

Each page is cut into clauses; a clause goes to the model only if it holds a
number with a currency, or a number next to a word of buying, paying or giving
(48 clauses from 23 of the first 34 days, 8,000 characters: two requests). First
run, 2026-10-03, free model: 18 amounts in 55 seconds; of the 16 checked against
the text, 15 right — the wrong one was a fare another passenger paid (the prompt
now names that case). The model returns payments with the
number of the clause they come from: amount (two pants at 1000 each is 2000),
paid or received ("gives me 1000" is received; what others pay each other is
not yours), category. They become *suggestions* with a fingerprint (day, clause,
amount, direction, rank among equal amounts): added, dismissed or pending, a
suggestion is never proposed twice, and an amount already entered by hand that
day is not proposed. Editing a sentence replaces its pending suggestion. The
server reads pages that changed once they have been quiet for 10 minutes, at most
every 30 minutes (one request for up to ~6,000 characters of clauses); "Add
without asking" turns suggestions into transactions at once.

## Time from the journal (`services/journal_time.py`)

The same idea for time: only the lines of a page that hold a time (`13:30`, `9h`,
`2 hours`, `45 mn`) go to the model — a very long line keeps just its clauses with
a time, each with the clause before it ("then" needs its antecedent) — and a page
is read again only when *those lines* change. The model returns blocks (what,
start, end, category, people present, approximate or not) and the people met,
numbered by the line they come from; each day becomes one draft in the Capture
inbox, built with the same `normalize()` as a capture. A block that an entry of
the ledger already covers (start and end within 10 minutes) arrives unticked
with a note, so reading a page again never duplicates what was saved. Saved
blocks get the source `journal`: your own account of the day, ranked with the
entries you type. Private pages are never sent to a cloud model. First run,
2026-10-04, free model: 35 pages in 2.5 minutes, 39 blocks on 23 days; 38 of
them carry exactly the times written.

## Undo (`services/undo.py`)

Every assistant action records what reverses it: the id it created, the statuses
a habit log replaced, a goal's previous values, the exact text appended to a
journal page. A committed capture records every id it created, so the whole
capture can be undone; the draft returns to the inbox. Undo refuses rather than
guess when the record was edited by hand since (text appended to the journal and
then changed is not cut blindly). Undoing a journal append rewrites the file too.

## Prayer times (`services/prayer.py`)

Astronomy, offline: the sun's declination and the equation of time (the USNO
low-precision formulas PrayTimes.org uses) give solar noon; the hour angle at which
the sun reaches a given altitude gives Fajr and Isha (18°/17° below the horizon,
Muslim World League), sunrise and Maghrib (0.833°), and Asr (shadow ratio 1, or 2
for Hanafi). Two refinements with the sun's position at the time found. Checked
against Aladhan's timetable for Niamey on 2026-10-03 and 2027-06-21: identical
to the minute, except Asr once (one minute, rounding). Applying a template moves
its prayer blocks (Fajr, Dhuhr or Jumu'ah, Asr, Maghrib, Isha) to the day's times
plus the person's offsets; other blocks give way (the overlap is cut out).

## Reminders (`services/reminders.py`, `services/notify.py`)

A loop in the server checks once a minute what is due: *capture* (bed time − 30
min), *bedtime*, *morning* (wake time), each once per day, skipped when more than
45 minutes late (the laptop was off). Each morning yesterday's review is stored
(`day_reviews`): the facts always, the prose when a model answers. Toasts are
Windows notifications shown through PowerShell 5.1's WinRT bridge — a fixed
script on standard input, the text in an environment variable as XML — under the
app name "OwnLife" (registered in `HKCU`, no administrator rights). Clicking one
opens the right page (`/?capture=1`, the review of yesterday).

## The YouTube extension (`extension/`, `services/watchlive.py`)

A Manifest V3 extension. The content script watches YouTube's main player only
(not the silent previews) and sends a heartbeat every 15 s while it plays, and
when it starts, pauses or ends. The service worker queues heartbeats in
`chrome.storage` and posts them in batches to `/api/ingest/youtube` with a
device key (`api_tokens`: SHA-256 stored, scope `youtube`, revocable; it opens
nothing else). On the server, heartbeats become runs per video (a gap over 45 s
ends a run), runs become *segments* (merged with any stored segment they touch,
so late or out-of-order batches still give one segment), and segments become
ledger blocks: same category, less than 3 minutes apart → one block. Channel
names come from the page, else from YouTube's public oEmbed endpoint.

## YouTube history files (`services/youtube.py`)

One import box takes YouTube's history (Takeout or My Activity, JSON or HTML,
English or French) and Chrome's (`History.json`). The HTML parser reads each
entry's verb: only *watched* lines become watch events. Likes, subscriptions,
searches and shares, which My Activity mixes in, are counted and reported, so an
export made while YouTube was not keeping watches is explained rather than empty.
Chrome lines are YouTube video pages (a reopening of the same video within 10
minutes is a reload); their channels are looked up afterwards in a background
thread — six requests at a time, no database session open while waiting — and the
estimated blocks are rebuilt so channel rules apply. Durations: a video lasts
until the next one if that is within 20 minutes, else 8 minutes; lines of two
files within 15 minutes for the same video count once. Estimated blocks no longer
skip a day that has other entries: precedence (above) resolves the overlaps.

## Search (RAG retrieval)

1. **Chunking** follows your structure: paragraphs and numbered thoughts (`1)`,
   `//`, `-->`), packed into ~1,200-character chunks.
2. **Keyword**: FTS5 with BM25, accent-insensitive (`zoe` finds `Zoé`).
3. **Semantic**: `embeddinggemma` vectors (768 dimensions), computed locally in a
   background thread; cosine similarity by numpy over the whole matrix — for a few
   thousand chunks that is faster than any vector database is to set up.
4. **Fusion**: Reciprocal Rank Fusion, `1 / (60 + rank)` from each list — no score
   calibration needed between two incomparable scales.

Measured 2026-10-02: 33 days + 4 notes = 160 chunks, embedded in under a minute.

## The assistant (LangGraph)

```text
START → retrieve → agent ⇄ tools → END
```

- **retrieve** — if the message looks like a question about the past, the hybrid
  search runs *locally* and the best passages go into the system prompt. Most
  memory questions are then answered in one model request instead of two.
- **agent** — the chat model with 21 tools bound:
  - read: `search_memory`, `get_time_summary`, `get_day`, `list_goals`,
    `get_life_numbers`, `get_youtube`, `get_prayer_times`, `get_spending`,
    `get_habit_progress`, `list_life_events`;
  - write (each undoable): `log_time`, `start_timer`, `stop_timer` (or pause),
    `resume_timer`, `log_expense`, `log_habit`, `add_to_journal`, `update_goal`,
    `create_goal`, `add_life_event`, `plan_block`.

  For a cloud model, tools leave out what is private: private journal pages,
  entries and life events, and the titles and channels of videos in a
  *destructive* category (only their minutes are given).
- **tools** — LangGraph's `ToolNode` runs the calls, then loops back (max 4 rounds).

The system prompt is built from live data: today's numbers, your categories
(`Mathematics (core), …`), habits, vocabulary. Answers stream to the browser as
Server-Sent Events. History is stored in OwnLife's own tables, not a checkpointer.

Models: `llm.chat_models()` builds OpenRouter (`models` array = server-side
fallback between free models) then Ollama (`with_fallbacks` = client-side
fallback). Errors are translated into something actionable (rate limit, privacy
settings, offline).

Measured 2026-10-02 with the local `qwen3:4b-instruct` (no cloud): logging a block
took 46 s with the model cold and 16 s once loaded.

## Capture: free text → reviewed records

`ai/capture.py`: the model fills a Pydantic schema (time entries, money, habits,
media, people) via function calling (cloud) or JSON-schema decoding (Ollama).
`normalize()` maps names to ids and flags doubts; the draft is stored; **nothing is
written until you press Save**. Measured on a real day with the local 4B model
(247 s): it misfiled study hours as work, invented two small expenses and
proposed a habit status that the text never mentions. Since then a relapse or a
"missed" proposed by a model arrives **unticked**, computed habits never receive
hand statuses, and the prompt forbids inferring them. Cloud models are much better
at this; the review step stays either way.

## Privacy

| Stays on the machine | Can leave (cloud AI mode only) |
| --- | --- |
| The database, backups, embeddings, search, quick-log, every chart | Your chat message, today's totals, the ≤ 5 passages relevant to the question, after redactions |
| Private journal pages and entries, private glossary meanings | Category and habit **names** (your aliases, e.g. `{X}-free`) |

Redactions (Settings → Vocabulary & privacy) replace words before anything is sent,
e.g. an explicit word by its alias `{X}`.

## Security

- Passwords: argon2. Sessions: random token in an httpOnly, SameSite=Lax cookie;
  only its SHA-256 is stored, so a copy of the database cannot log anyone in.
- CSRF: every state-changing request must carry `X-OwnLife: 1`, which another site
  cannot add without a CORS preflight this server never grants.
- Login throttle (8 failures / 5 minutes), constant-time response for unknown
  emails, security headers + Content-Security-Policy on the served frontend.
- `IMPORT_ROOT`: the server reads and writes Markdown only under your notes
  folder, and keeps the previous version of a file before every write.
- Device keys (the YouTube extension): random, shown once, stored as SHA-256,
  scoped (`youtube` can only send watch time), revocable in Settings.

## Smart App Control

Enforced on this laptop: unsigned compiled files it does not know get blocked —
including pip's `.exe` launchers, hence `python -m …` everywhere. Checked
2026-10-02: every compiled Python dependency (pydantic-core, uuid-utils, orjson,
jiter, tiktoken, argon2, numpy…) and the frontend's native tools (Rolldown,
Tailwind's Oxide, LightningCSS) load. TypeScript is pinned to 5.9 because 7.x
ships native binaries. `python -m app.doctor` re-checks all of this. Notifications
go through Windows PowerShell 5.1 (signed by Microsoft); the background start uses
the venv's `pythonw.exe` (signed by the Python Software Foundation).

## Tests

- Backend: `python -m pytest tests -q` — 74 tests; each uses a fresh temporary
  database, a throwaway account and its own folder of Markdown files shaped like
  the real journal (CRLF, trailing spaces, no final newline); AI off unless a
  scripted fake model is injected, ActivityWatch mocked, notifications sent to a
  fake. Covers auth, time maths across midnight, which source wins, the two-way
  sync (minimal writes, conflicts, stale editors, vanished days, notes, the live
  watcher), undo, the extension's heartbeats (out of order included), prayer
  times against a published timetable, the reminders' schedule, life events,
  imports, projections, habits, goals, plans, money, export/backup, YouTube,
  ActivityWatch, hybrid search, the agent loop, capture and its safety defaults.
- The extension's scripts were run against a throwaway server with a fake
  `chrome` API (and `content.js` in a YouTube-like page): heartbeats, the queue
  while OwnLife is down, delivery afterwards, a revoked key.
- Frontend: `npm test` (time-zone and formatting logic), `npm run typecheck`.
