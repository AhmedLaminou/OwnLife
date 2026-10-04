# Going live: what to fill in

The tests run against fakes (a scripted model, a mocked ActivityWatch, temporary
databases and files, a fake notifier). The app itself talks to the real
services. Here is everything you need to provide, in order of importance. Check
at any time with:

```powershell
cd backend; .\.venv\Scripts\python.exe -m app.doctor --online
```

## 1. Your account and your seed — two minutes

- First start → create your account (your password; nobody can recover it but
  `python -m app.cli reset-password` on this machine).
- **Settings → Data → Personal seed → Apply.** It adds what
  `backend/data/personal_seed.json` describes (git-ignored, written for you):
  chapters, the dated moments of your life (Life → Moments), goals, habits,
  people, classification rules and day templates; it can also remove generic
  templates your own ones replace (only if you never edited them). Applying it
  again only adds what is missing. Then check the dates marked *approximate*
  (Life → Chapters, Moments).

## 2. Your files — nothing to do

The journal and the notes beside it are synced **both ways**, live:

- Save `2026_JOURNAL.md` in VS Code → OwnLife has it within ~2 seconds.
- Edit a day or a note in OwnLife → the file gets exactly the changed lines,
  every other byte (CRLF line endings, the two trailing spaces of your Markdown
  line breaks, the missing final newline) left as it was.
- Before every write, the previous version is copied to
  `backend\data\file-history\`. A day changed on both sides at once is never
  overwritten: Journal shows both versions and you choose (or merge by hand).
- A day whose header disappears (a typo: `Dya 12 : …`) is hidden, not deleted;
  fix the header and it comes back as it was.
- `JOURNAL_SYNC_PATH` uses `{year}`: in 2027 a page goes to
  `2027\2027_JOURNAL.md` (created if needed); `.md` files beside
  each year's journal are your notes, and a note created in OwnLife becomes a file
  there.

`FILE_SYNC_WRITEBACK=false` in `backend\.env` makes OwnLife read-only towards your
files; `FILE_SYNC=false` stops the watching (Journal → *Sync now* still works).

## 3. Keep OwnLife running — for the reminders

The evening ritual (22:30 *capture your day*, 23:00 bedtime guard, the morning
summary and yesterday's review) is sent by OwnLife itself, so it must be running:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\autostart.ps1      # start with Windows, no window
```

Then **Settings → Rituals → Send a test notification**. If nothing appears,
Windows' *Do not disturb* / Focus is probably on (Settings → System →
Notifications): OwnLife appears there as an app you can allow. The times follow
your profile's bed and wake times; every reminder can be switched off.

## 4. OpenRouter — the cloud AI (free models)

**Needed for:** fast chat, capture and reviews. Without it, the assistant uses the
local Ollama model (works, private, but 15–250 s per answer on this laptop).

1. Create a key at **<https://openrouter.ai/keys>** (it starts with `sk-or-`).
2. Put it in `backend\.env`: `OPENROUTER_API_KEY=sk-or-...`.
3. Privacy settings at **<https://openrouter.ai/settings/privacy>**: free endpoints
   are usually run by providers that log prompts. If a request fails with *"No
   endpoints found matching your data policy"*, allow free endpoints there — or stay
   in local mode for the most private use. That trade-off is why OwnLife only ever
   sends the passages relevant to a question, after your redactions.
4. Restart the app. Settings → AI should show *Chain: OpenRouter · nvidia/… → Ollama*.

Free tier limits (checked 2026-10-02): **20 requests/minute, 50/day**; the daily
cap becomes **1000/day** once the account has bought $10 of credits in total (a
one-time purchase; free models still cost nothing). A chat answer costs 1–3
requests, a capture 1, a daily review 1 (the automatic morning review included).

Free models change. The defaults (`OPENROUTER_MODELS` in `.env`) are tried in order:

```text
nvidia/nemotron-3-super-120b-a12b:free,qwen/qwen3.8-27b:free,google/gemma-4-31b-it:free
```

If `doctor --online` reports one missing from the catalog, replace it (any model
whose id ends in `:free` and supports *tools* — <https://openrouter.ai/models>).

## 5. YouTube — the extension for now on, Takeout for the past

YouTube gives no app your watch history (removed from its API in 2016). So:

- **From now on, on this laptop → the OwnLife extension** (`extension/` folder):
  1. Chrome or Edge → `chrome://extensions` (or `edge://extensions`) → turn on
     **Developer mode** → **Load unpacked** → choose `OwnLife\extension`. Not
     *Pack extension*: that produces a `.crx` and a `.pem` (the private key that
     signs a packed extension). Neither is needed, nothing is imported into the
     browser, and both are git-ignored if they exist.
  2. OwnLife → **Settings → Integrations → YouTube extension → Create a key**.
     In the browser: right-click the extension's icon → **Options** → paste the
     key → **Save**; it answers "Connected". The key lives in the extension from
     then on: do not keep it in a file (OwnLife stores only its SHA-256; a lost
     key is revoked and replaced in one click).
  3. Play a video: its real minutes (pauses excluded) appear in the ledger within
     a minute, filed by your channel rules. While OwnLife is off, the extension
     keeps the minutes and sends them later.
- **The past → Google Takeout** (<https://takeout.google.com>), one of two files,
  imported in Watching → **Import a history**:
  - YouTube's own history: *Deselect all* → *YouTube and YouTube Music* → keep only
    **history** → *Multiple formats* → History: **JSON** → `watch-history.json`.
    It exists only if YouTube keeps what you watch: myactivity.google.com →
    *YouTube History* → "Include the videos you watch on YouTube". When it is off,
    the export holds searches only, and the import says so.
  - Chrome's history: *Chrome* → `History.json` (`Historique.json` in French) —
    the YouTube pages opened in that browser over the recent weeks. It has no
    channel names: OwnLife looks them up in the background (YouTube's public
    oEmbed endpoint), then files the videos by your channel rules.

  Re-importing is safe: duplicates are ignored, and a video both files saw counts
  once. Durations are *estimated* from the gaps between videos; where the
  extension measured the same video, the measurement wins, and your own entries
  win over both.

Then give categories to your top channels (Watching → Channels): each choice
becomes a rule, and the history and measured minutes are re-filed.

## 6. ActivityWatch — the rest of the laptop (optional)

Records which app and which website is on screen, minute by minute, and whether
you are at the keyboard. Local and open source.

1. Install from **<https://activitywatch.net/downloads/>** and start it (tray icon).
   Smart App Control is on on this laptop: if it blocks ActivityWatch (I could not
   confirm whether its Windows files are signed), YouTube is still covered by the
   extension above — and a small built-in window tracker is on the roadmap.
2. Its browser extension (*ActivityWatch Web Watcher*) is optional now: the
   OwnLife extension measures YouTube more exactly, and where both saw the same
   minutes, they are counted once.
3. `ACTIVITYWATCH_AUTOSYNC_MINUTES=15` in `backend\.env`, then Settings →
   Integrations → **Sync now** once; it syncs every 15 minutes after that.

Unknown apps and sites arrive uncategorised: add rules in Settings → Categories &
rules (e.g. `app Code.exe → Building projects`, `domain kaggle.com → AI & ML`).

## 7. Ollama — already done

`embeddinggemma` (search) and `qwen3:4b-instruct-2507-q4_K_M` (offline chat) are
installed. Ollama loads a model only when asked and unloads it after a few idle
minutes — an empty `ollama ps` is normal.

## 8. Prayer times — check once

Settings → Rituals → Prayer times: computed offline for Niamey (Muslim World
League, standard Asr) — checked against a published timetable to the minute.
Mosques pray a little after the adhan: put your mosque's delay in the per-prayer
offsets, and applied templates will place Fajr, Dhuhr, Jumu'ah, Asr, Maghrib and
Isha at those times every day.

## 9. Backups

Automatic once a day into `backend\data\backups` (14 kept), and one before every
database upgrade. Copy that folder to a USB key or a cloud drive now and then:
the laptop is the only copy otherwise. Consider turning on BitLocker: the
database holds your journal in clear.
