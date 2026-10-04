# Roadmap

## Done

**v0.1 (2026-10-02)** — auth, profile, ledger with timer and quick-log, journal
import and hybrid search, life-in-weeks and projections, planner with templates
and adherence, goals with invested hours, habits (rule-based and quit), YouTube
Takeout import, ActivityWatch sync, money, people, LangGraph assistant with 13
tools and RAG, capture with review, daily review, backups, export, CLI, doctor.

**v0.2 (2026-10-03)** — less typing, more truth:

- **Two-way live sync with the Markdown files**: a save in VS Code reaches
  OwnLife in seconds; an edit in OwnLife writes only the changed lines into the
  file; conflicts are shown, never overwritten; vanished days are hidden, not lost;
  `{year}` files; notes created, edited and deleted from the app; file history.
- **The OwnLife YouTube extension**: real minutes per video, measured in the
  browser, queued while OwnLife is off; device keys.
- **No double counting**: your entries, then measured, then ActivityWatch, then
  estimates; disagreements ("YouTube during Sleep") pointed out.
- **Evening ritual**: Windows notifications for *capture your day*, bedtime, and a
  morning summary; yesterday's review written and stored each morning.
- **Prayer times** computed offline (checked to the minute), templates aligned.
- **Life events**: the dated moments, on the grid and in a timeline.
- **Undo** for every assistant action and for whole captures.
- Capture in one place (*One entry* joined *Tell the assistant* and *Quick log*);
  the personal seed applied from Settings; start with Windows, without a window;
  a backup before every database upgrade. 74 backend tests.

**v0.2.1 (2026-10-03)** — what the first real Takeout showed:

- **Chrome's history imported** for YouTube (the only record of watches when
  YouTube's own history is off), channels looked up in the background.
- History files read correctly: French HTML dates, and likes, subscriptions and
  searches no longer mistaken for watches; an export without watches says why.
- An estimate no longer vanishes on a day that has other entries.
- **7 more assistant tools**: YouTube watched, prayer times, spending, habit
  streaks, life events (read and add), plan blocks. 86 backend tests.

**v0.2.2 (2026-10-03)** — the first review with real use:

- **Timer pause and resume**: segments of one session; several timers can wait
  paused; the assistant can pause and resume too.
- **A forgotten timer no longer swallows YouTube**: measured videos of a known
  category count over a running timer (your typed entries still win).
- **Money from the journal**: the sentences that mention money are read by the
  model and proposed on the Money page (18 found in the first 34 days); one click
  to add, never proposed twice; optionally added without asking.
- A test that the migrations build exactly the models. 100 backend tests.

## Next, in the order that matters most

1. **Go live** — apply the seed, load the extension, run `scripts\autostart.ps1`,
   import Chrome's history once ([GOING_LIVE.md](GOING_LIVE.md)), and turn on
   YouTube's watch history so the next exports carry the phone's videos.
2. **A Google activity timeline** — the rest of a Takeout as dated lines per day:
   Gemini prompts, Chrome pages, searches. Searchable by the assistant
   (`search_activity`), shown under each day, private by default for cloud models.
   Waiting on a decision: which of those OwnLife should hold at all.
3. **A built-in window tracker**, if Smart App Control blocks ActivityWatch: the
   active app and window title, sampled by OwnLife itself (Windows API through
   `ctypes`, nothing to install), classified by the same rules.
4. **Backfill the 33 days** — Journal → *Extract to ledger* on each day, with a
   cloud model, reviewing each draft. A "batch extract" queue would make it faster.
5. **Weekly and monthly reviews** — the daily reviews, aggregated: trend of core
   hours, noise, sleep regularity, adherence, the relapse-hour chart.
6. **Encryption at rest** — SQLCipher, or at least BitLocker on the drive: the
   database holds the journal in clear.

## Later

- **The phone** — a PWA over the home Wi-Fi (installable, offline queue), then
  anywhere: capture in 30 seconds when it is in the pocket. Device keys already
  exist for it.
- Goal ↔ habit links (milestone "6 months clean" completing itself from the streak).
- Study tools for [Sprint]: courses with lecture progress, spaced-repetition cards.
- Charts: hours by weekday × hour heatmap, year over year.
- Firefox build of the extension (Manifest V3 background scripts).
- Frontend: split the 880 kB bundle per page; component and end-to-end tests.
- YouTube Data API key (optional) for exact video lengths and YouTube's own categories.

## If it becomes a SaaS

PostgreSQL + pgvector (every table already carries `user_id`), per-user API keys
or a pooled key with quotas, email verification and password reset by mail,
registration and billing, hosting with HTTPS (`COOKIE_SECURE=true`), a
data-deletion endpoint next to the existing export, onboarding templates instead
of a personal seed file — and file sync through a desktop companion (or a synced
folder per account) instead of a server reading a local folder.
