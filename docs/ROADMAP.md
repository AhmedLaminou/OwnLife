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

**v0.2.3 (2026-10-04)** — on GitHub, and less to type:

- **The code on GitHub** (personal data scrubbed: tests use invented examples).
- **Backups off this disk**: `BACKUP_MIRROR_DIR` gets a copy within the hour.
- **Time from the journal**: the lines with a time become one draft per day in
  the inbox (39 blocks on 23 days the first time); private pages never go to a
  cloud model, for money either.
- **The timer forgives**: started or stopped "minutes ago", and "still on it?"
  after 2 hours.
- **A built-in window tracker**: the program and window in front, every 5 s while
  the keyboard or mouse is used; ActivityWatch no longer needed. 121 backend tests.

**v0.2.4 (2026-10-05)** — the noise budget, live:

- **A notification as the budget runs out**: 15 minutes before, when it is
  spent, every 15 minutes past it; the extension's icon shows the day's noise.
- **Strict mode**: YouTube that nothing sorted counts as noise.
- **The local model sorts new videos** (title and channel, on this laptop); what
  it cannot tell waits on the Watching page, one click to confirm; a whole channel
  in one click. Videos that arrived without their channel get it back.
- Smart App Control started blocking `jiter`, which broke every AI feature: a
  pure-Python stand-in takes its place.

**v0.2.5 (2026-10-05)** — people, money by day, ideas for the essays:

- **Money day by day**: today, yesterday, then each date with its totals; a click
  on the chart opens a day; transactions can be corrected; the same amount twice
  on a day is flagged.
- **People with what went between you**: money linked to the person who gave or
  received it (added when new, asked when only close), gifts, moments, merging two
  entries for one person; Capture and the assistant fill them too.
- **31 tools for the assistant** (people, money line by line, the library, notes
  and essays, ideas, rules), the local fallback with a short list of 12.
- **Ideas from the [SomeThoughts] sections** for the essays — with references
  checked in Open Library, placed as sections, undoable; passages per essay found
  locally. Reading by itself is off until switched on.
- **The YouTube history sorted by the local model** on demand.

## Next, in the order that matters most

1. **Go live** — apply the seed, run `scripts\autostart.ps1`, set
   `BACKUP_MIRROR_DIR`, save the 23 journal drafts in the inbox, and turn on
   YouTube's watch history so the next exports carry the phone's videos
   ([GOING_LIVE.md](GOING_LIVE.md)).
2. **Browser tabs by address** — the window tracker knows a tab only by its
   title; the OwnLife extension can report the active tab's site, so domain rules
   classify browsing as they classify YouTube (and news sites count against the
   noise budget as surely as news videos).
3. **Google Takeout, the rest**: YouTube subscriptions (pre-sort the channels you
   chose), Classroom (the university classes as courses, a chapter on the Life
   page), Calendar.
4. **A screen over noise once the budget is spent** — optional: the extension
   covers a noise video with "your hour is spent", one button for ten more
   minutes; learning videos never blocked.
5. **A Google activity timeline** — the rest of a Takeout as dated lines per day:
   Gemini prompts, Chrome pages, searches. Searchable by the assistant
   (`search_activity`), shown under each day, private by default for cloud models.
   Waiting on a decision: which of those OwnLife should hold at all.
6. **Weekly and monthly reviews** — the daily reviews, aggregated: trend of core
   hours, noise, sleep regularity, adherence, the relapse-hour chart.
7. **Encryption at rest** — SQLCipher, or at least BitLocker on the drive: the
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
