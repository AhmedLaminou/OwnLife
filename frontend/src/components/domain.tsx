import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Check, Pause, Play, Plus, Sparkles, Square, Trash2, Wand2 } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { clock, fromLocalInput, hm, localDate, toLocalInput } from "../lib/format";
import { useCategories, useNow, useProfile, useTimeZone, useToday } from "../lib/hooks";
import { CHART_KINDS, KIND_LABEL, kindColor } from "../lib/kinds";
import type { CaptureDraft, CaptureDraftBody, Category, CommitResult, QuickResult, TimeEntry, TimerEntry, TimerState } from "../lib/types";
import { Badge, Button, ErrorNote, Field, Input, Modal, Select, Tabs, Textarea, Toggle, useToast } from "./ui";

/** Everything that depends on the ledger: one call refreshes all of it after a write. */
export function useInvalidateLedger() {
  const qc = useQueryClient();
  return () => {
    for (const key of ["dashboard", "day", "stats", "entries", "timer", "habits", "life", "goals", "plan", "people", "money"]) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  };
}

// ---------------------------------------------------------------- category select
export function CategorySelect({
  value,
  onChange,
  allowNone = true,
  noneLabel = "Uncategorised",
  className,
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  allowNone?: boolean;
  /** The text of the empty choice. */
  noneLabel?: string;
  className?: string;
}) {
  const { data: cats = [] } = useCategories();
  const groups = useMemo(() => {
    const order = [...CHART_KINDS, "destructive"];
    return order
      .map((k) => ({ kind: k, items: cats.filter((c) => c.kind === k) }))
      .filter((g) => g.items.length);
  }, [cats]);
  return (
    <Select value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)} className={className}>
      {allowNone && <option value="">{noneLabel}</option>}
      {groups.map((g) => (
        <optgroup key={g.kind} label={g.kind === "destructive" ? "Quitting" : KIND_LABEL[g.kind as keyof typeof KIND_LABEL]}>
          {g.items.map((c: Category) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </optgroup>
      ))}
    </Select>
  );
}

export function KindDot({ kind, className }: { kind: string; className?: string }) {
  return <span className={clsx("inline-block h-2.5 w-2.5 shrink-0 rounded-full", className)} style={{ background: kindColor(kind) }} />;
}

// ---------------------------------------------------------------- entry form
/** One block of time: what, category, when, with whom. Used for a new entry
 *  (Capture → One entry) and to edit any entry (click it in the Ledger). */
export function EntryForm({ entry, defaultDay, onDone }: { entry?: TimeEntry | null; defaultDay?: string; onDone: () => void }) {
  const tz = useTimeZone();
  const today = useToday();
  const toast = useToast();
  const invalidate = useInvalidateLedger();
  const day = defaultDay ?? today;
  const [form, setForm] = useState(() => {
    if (entry) {
      return {
        title: entry.title,
        category_id: entry.category_id,
        start: toLocalInput(entry.started_at, tz),
        end: entry.ended_at ? toLocalInput(entry.ended_at, tz) : "",
        people: entry.people.map((p) => p.name).join(", "),
        location: entry.location ?? "",
        notes: entry.notes ?? "",
        is_private: entry.is_private,
      };
    }
    const now = new Date();
    const h = clock(now, tz);
    const isToday = day === localDate(now, tz);
    return {
      title: "",
      category_id: null as number | null,
      start: `${day}T${isToday ? h : "09:00"}`,
      end: `${day}T${isToday ? h : "10:00"}`,
      people: "",
      location: "",
      notes: "",
      is_private: false,
    };
  });

  const save = useMutation({
    mutationFn: () => {
      const names = form.people.split(",").map((s) => s.trim()).filter(Boolean);
      const body = {
        title: form.title.trim(),
        category_id: form.category_id,
        started_at: fromLocalInput(form.start, tz),
        ended_at: form.end ? fromLocalInput(form.end, tz) : null,
        location: form.location || null,
        notes: form.notes || null,
        is_private: form.is_private,
      };
      if (entry) {
        const keep = entry.people.filter((p) => names.includes(p.name));
        const fresh = names.filter((n) => !entry.people.some((p) => p.name === n));
        return api.patch(`/api/time/entries/${entry.id}`, { ...body, person_ids: keep.map((p) => p.id), new_people: fresh });
      }
      return api.post("/api/time/entries", { ...body, new_people: names });
    },
    onSuccess: () => {
      invalidate();
      toast(entry ? "Entry updated" : "Entry logged", "good");
      onDone();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/time/entries/${entry!.id}`),
    onSuccess: () => {
      invalidate();
      toast("Entry deleted");
      onDone();
    },
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label="What">
        <Input autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Linear algebra — chapter 3" required />
      </Field>
      <Field label="Category">
        <CategorySelect value={form.category_id} onChange={(v) => setForm({ ...form, category_id: v })} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Start">
          <Input type="datetime-local" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} required />
        </Field>
        <Field label="End" hint="Empty = still running">
          <Input type="datetime-local" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="With" hint="Names, comma-separated">
          <Input value={form.people} onChange={(e) => setForm({ ...form, people: e.target.value })} placeholder="Sam, Lea" />
        </Field>
        <Field label="Where">
          <Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} placeholder="Office, library" />
        </Field>
      </div>
      <Field label="Notes">
        <Textarea rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </Field>
      <Toggle checked={form.is_private} onChange={(v) => setForm({ ...form, is_private: v })} label="Private — never sent to a cloud model" />
      {entry && entry.source !== "manual" && (
        <p className="text-[12px] text-ink-3">
          From {SOURCE_LABEL[entry.source] ?? entry.source}
          {entry.is_estimate ? " — the duration is an estimate" : ""}. A category chosen here stays when the rules change.
        </p>
      )}
      <ErrorNote error={save.error ?? remove.error} />
      <div className="flex items-center justify-between gap-2 pt-1">
        {entry ? (
          <Button type="button" variant="ghost" icon={<Trash2 size={15} />} onClick={() => remove.mutate()} loading={remove.isPending}>
            Delete
          </Button>
        ) : (
          <span />
        )}
        <Button type="submit" variant="primary" loading={save.isPending} icon={<Check size={16} />}>
          Save
        </Button>
      </div>
    </form>
  );
}

export const SOURCE_LABEL: Record<string, string> = {
  manual: "your form",
  timer: "the timer",
  quick: "quick-log",
  ai: "the assistant",
  journal: "your journal",
  window: "the window tracker",
  activitywatch: "ActivityWatch",
  extension: "the YouTube extension (measured)",
  youtube_takeout: "your YouTube or Chrome history (estimated)",
};

export function EntryModal({
  open,
  onClose,
  entry,
  defaultDay,
}: {
  open: boolean;
  onClose: () => void;
  entry?: TimeEntry | null;
  defaultDay?: string;
}) {
  return (
    <Modal open={open} onClose={onClose} title={entry ? "Edit entry" : "Log time"}>
      {open && <EntryForm key={entry?.id ?? `new-${defaultDay ?? ""}`} entry={entry} defaultDay={defaultDay} onDone={onClose} />}
    </Modal>
  );
}

// ---------------------------------------------------------------- timer
/** The running timer and the paused ones. */
export function useTimer() {
  return useQuery({ queryKey: ["timer"], queryFn: () => api.get<TimerState>("/api/time/timer/state"), refetchInterval: 60_000 });
}

/** The moment a session would have started had it never paused, so that
 * <Elapsed> counts the whole session (fetchedAt: when the state was read). */
export function sessionSince(t: TimerEntry, fetchedAt: number): string {
  const segment = Math.max(0, (fetchedAt - Date.parse(t.started_at)) / 1000);
  const before = Math.max(0, t.session_seconds - segment);
  return new Date(Date.parse(t.started_at) - before * 1000).toISOString();
}

export function Elapsed({ since }: { since: string }) {
  const now = useNow(1000);
  const s = Math.max(0, (now.getTime() - Date.parse(since)) / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return (
    <span className="num">
      {h > 0 && `${h}:`}
      {String(m).padStart(h > 0 ? 2 : 1, "0")}:{String(sec).padStart(2, "0")}
    </span>
  );
}

const AGO_CHOICES = [0, 5, 10, 15, 20, 30, 45, 60, 90, 120];

/** "now", or so many minutes ago — for a timer started or stopped late. */
function AgoSelect({ value, onChange, now, label }: { value: number; onChange: (v: number) => void; now: string; label: string }) {
  return (
    <Select value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} title={label} className="w-auto text-[13px]">
      {AGO_CHOICES.map((m) => (
        <option key={m} value={m}>{m === 0 ? now : `${m} min ago`}</option>
      ))}
    </Select>
  );
}

export function TimerCard() {
  const { data, dataUpdatedAt } = useTimer();
  const tz = useTimeZone();
  const invalidate = useInvalidateLedger();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [cat, setCat] = useState<number | null>(null);
  // "I started 20 minutes ago", "I stopped 10 minutes ago": forgetting the button is normal.
  const [startedAgo, setStartedAgo] = useState(0);
  const [stoppedAgo, setStoppedAgo] = useState(0);
  const start = useMutation({
    mutationFn: () => api.post("/api/time/timer/start", { title: title.trim() || "Deep work", category_id: cat, minutes_ago: startedAgo }),
    onSuccess: () => {
      invalidate();
      setTitle("");
      setStartedAgo(0);
    },
  });
  const stop = useMutation({
    mutationFn: () => api.post<TimerEntry | null>(`/api/time/timer/stop?minutes_ago=${stoppedAgo}`),
    onSuccess: (e) => {
      invalidate();
      setStoppedAgo(0);
      if (e) toast(`Logged ${hm(e.session_seconds)} — ${e.title}`, "good");
    },
  });
  const pause = useMutation({
    mutationFn: () => api.post<TimerEntry>(`/api/time/timer/pause?minutes_ago=${stoppedAgo}`),
    onSuccess: (e) => {
      invalidate();
      setStoppedAgo(0);
      toast(`Paused at ${hm(e.session_seconds)} — the time until you resume is not counted`);
    },
  });
  const resume = useMutation({
    mutationFn: (id: number) => api.post<TimerEntry>("/api/time/timer/resume", { entry_id: id }),
    onSuccess: () => invalidate(),
  });
  const finish = useMutation({
    mutationFn: (id: number) => api.post<TimerEntry>(`/api/time/timer/finish/${id}`),
    onSuccess: (e) => {
      invalidate();
      toast(`Logged ${hm(e.session_seconds)} — ${e.title}`, "good");
    },
  });
  const running = data?.running ?? null;
  const paused = data?.paused ?? [];
  return (
    <div className="space-y-3">
      <AnimatePresence mode="wait" initial={false}>
        {running ? (
          <motion.div key="running" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }} className="space-y-3">
            <div className="flex items-center gap-2 text-[13px] text-ink-3">
              <span className="h-2 w-2 animate-pulse-soft rounded-full bg-amber" /> Running
            </div>
            <p className="truncate text-lg font-semibold text-ink">{running.title}</p>
            <p className="text-4xl font-semibold tracking-tight text-ink">
              <Elapsed since={sessionSince(running, dataUpdatedAt)} />
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {running.category && <Badge>{running.category.name}</Badge>}
              <div className="flex-1" />
              <Button variant="secondary" icon={<Pause size={14} />} onClick={() => pause.mutate()} loading={pause.isPending}>
                Pause
              </Button>
              <Button variant="amber" icon={<Square size={14} />} onClick={() => stop.mutate()} loading={stop.isPending}>
                Stop
              </Button>
            </div>
            <label className="flex items-center justify-end gap-2 text-[12px] text-ink-3">
              Forgot to press? It happened
              <AgoSelect value={stoppedAgo} onChange={setStoppedAgo} now="now" label="When you paused or stopped" />
            </label>
          </motion.div>
        ) : (
          <motion.form
            key="idle"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              start.mutate();
            }}
          >
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={paused.length ? "Something else meanwhile?" : "What are you starting?"} />
            <CategorySelect value={cat} onChange={setCat} />
            <div className="flex gap-2">
              <AgoSelect value={startedAgo} onChange={setStartedAgo} now="starting now" label="When you started" />
              <Button type="submit" variant="primary" className="flex-1" icon={<Play size={15} />} loading={start.isPending}>
                Start timer
              </Button>
            </div>
            <ErrorNote error={start.error} />
          </motion.form>
        )}
      </AnimatePresence>
      {paused.length > 0 && (
        <ul className="space-y-1.5">
          {paused.map((p) => (
            <li key={p.id} className="space-y-1.5 rounded-xl bg-panel-hover px-3 py-2 text-[13px]">
              <div className="flex items-center gap-2">
                <Pause size={13} className="shrink-0 text-ink-3" />
                <span className="min-w-0 flex-1 truncate font-medium text-ink">{p.title}</span>
                <span className="num shrink-0 text-ink-2">{hm(p.session_seconds)}</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="flex-1 pl-5 text-[12px] text-ink-3">paused at {clock(p.ended_at ?? p.started_at, tz)}</span>
                <Button size="sm" variant="ghost" icon={<Check size={13} />} onClick={() => finish.mutate(p.id)} loading={finish.isPending && finish.variables === p.id} title="Keep it as it is: it will not be resumed">
                  Done
                </Button>
                <Button size="sm" variant="primary" icon={<Play size={13} />} onClick={() => resume.mutate(p.id)} loading={resume.isPending && resume.variables === p.id}>
                  Resume
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={stop.error ?? pause.error ?? resume.error ?? finish.error} />
    </div>
  );
}

// ---------------------------------------------------------------- capture
export function CaptureReview({ draft, onDone, allowJournal = true }: { draft: CaptureDraft; onDone: () => void; allowJournal?: boolean }) {
  const [body, setBody] = useState<CaptureDraftBody>(draft.draft);
  const [appendJournal, setAppendJournal] = useState(false);
  const invalidate = useInvalidateLedger();
  const toast = useToast();
  const qc = useQueryClient();
  useEffect(() => setBody(draft.draft), [draft]);

  const commit = useMutation({
    mutationFn: () =>
      api.post<CommitResult>(`/api/ai/drafts/${draft.id}/commit`, {
        draft: { ...body, journal_text: allowJournal && appendJournal ? draft.input_text : null },
      }),
    onSuccess: (r) => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["drafts"] });
      qc.invalidateQueries({ queryKey: ["journal"] });
      const parts = Object.entries(r.created).filter(([, n]) => n).map(([k, n]) => `${n} ${k.replace("_", " ")}`);
      toast(parts.length ? `Saved: ${parts.join(", ")} — undo it from Assistant → Inbox` : "Nothing selected", "good");
      if (r.journal_sync?.state === "conflict") toast(r.journal_sync.message ?? "Journal: a conflict to resolve", "critical");
      if (r.errors.length) toast(r.errors.join(" · "), "critical");
      (r.warnings ?? []).forEach((w) => toast(w));
      onDone();
    },
  });
  const discard = useMutation({
    mutationFn: () => api.post(`/api/ai/drafts/${draft.id}/discard`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
      onDone();
    },
  });

  const set = <K extends keyof CaptureDraftBody>(key: K, i: number, patch: Record<string, unknown>) =>
    setBody((b) => ({ ...b, [key]: (b[key] as unknown[]).map((row, j) => (j === i ? { ...(row as object), ...patch } : row)) }));

  return (
    <div className="space-y-5">
      {body.summary && <p className="text-sm text-ink-2">{body.summary}</p>}
      {body.warnings.length > 0 && (
        <div className="space-y-1 rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-[13px] text-ink">
          {body.warnings.map((w) => (
            <p key={w} className="flex items-start gap-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning" /> {w}
            </p>
          ))}
        </div>
      )}

      {body.time_entries.length > 0 && (
        <section>
          <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Time</h3>
          <div className="space-y-2">
            {body.time_entries.map((t, i) => (
              <div key={i} className={clsx("grid grid-cols-[auto_1fr] items-start gap-3 rounded-xl border border-line p-3", !t.include && "opacity-45")}>
                <input type="checkbox" checked={t.include} onChange={(e) => set("time_entries", i, { include: e.target.checked })} className="mt-3 h-4 w-4 accent-[var(--accent)]" aria-label="Include" />
                <div className="grid gap-2 sm:grid-cols-[1fr_200px_90px_90px]">
                  <Input value={t.title} onChange={(e) => set("time_entries", i, { title: e.target.value })} />
                  <CategorySelect value={t.category_id} onChange={(v) => set("time_entries", i, { category_id: v })} />
                  <Input value={t.start} onChange={(e) => set("time_entries", i, { start: e.target.value })} aria-label="Start" className="num" />
                  <Input value={t.end} onChange={(e) => set("time_entries", i, { end: e.target.value })} aria-label="End" className="num" />
                  <div className="flex flex-wrap items-center gap-1.5 sm:col-span-4">
                    {t.crosses_midnight && <Badge tone="accent">ends after midnight</Badge>}
                    {t.day_offset === -1 && <Badge tone="accent">previous day</Badge>}
                    {t.approximate && <Badge tone="warning">approximate</Badge>}
                    {t.people.length > 0 && <Badge>with {t.people.join(", ")}</Badge>}
                    {t.location && <Badge>at {t.location}</Badge>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {body.transactions.length > 0 && (
        <section>
          <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Money</h3>
          <div className="space-y-2">
            {body.transactions.map((t, i) => (
              <label key={i} className={clsx("flex items-center gap-3 rounded-xl border border-line px-3 py-2 text-sm", !t.include && "opacity-45")}>
                <input type="checkbox" checked={t.include} onChange={(e) => set("transactions", i, { include: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
                <span className={clsx("num w-20 font-semibold", t.direction === "in" ? "text-good" : "text-ink")}>
                  {t.direction === "in" ? "+" : "−"}
                  {t.amount}
                </span>
                <span className="flex-1 text-ink-2">
                  {t.item}
                  {t.person ? <span className="text-ink-3"> · {t.direction === "in" ? "from" : "to"} {t.person}</span> : t.counterparty ? <span className="text-ink-3"> · {t.counterparty}</span> : null}
                </span>
                <Badge>{t.category}</Badge>
              </label>
            ))}
          </div>
        </section>
      )}

      {(body.moments ?? []).length > 0 && (
        <section>
          <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">For their page in People</h3>
          <div className="space-y-2">
            {(body.moments ?? []).map((m, i) => (
              <label key={i} className={clsx("flex items-start gap-3 rounded-xl border border-line px-3 py-2 text-sm", !m.include && "opacity-45")}>
                <input type="checkbox" checked={m.include} onChange={(e) => set("moments", i, { include: e.target.checked })} className="mt-0.5 h-4 w-4 accent-[var(--accent)]" />
                <span className="w-32 shrink-0 font-medium text-ink">{m.person}</span>
                <span className="flex-1 text-ink-2">
                  <span className="text-ink-3">{m.kind === "gift_from" ? "gave you: " : m.kind === "gift_to" ? "you gave: " : ""}</span>
                  {m.text}
                  {m.day_offset === -1 && <span className="text-ink-3"> (the day before)</span>}
                </span>
              </label>
            ))}
          </div>
        </section>
      )}

      {(body.habit_logs.length > 0 || body.people.length > 0 || body.media.length > 0) && (
        <section className="grid gap-4 sm:grid-cols-3">
          {body.habit_logs.length > 0 && (
            <div>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Habits</h3>
              {body.habit_logs.map((h, i) => (
                <label key={i} className="flex items-center gap-2 py-1 text-sm text-ink-2">
                  <input type="checkbox" checked={h.include} onChange={(e) => set("habit_logs", i, { include: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
                  {h.habit}: <span className="font-medium text-ink">{h.status}</span>
                </label>
              ))}
            </div>
          )}
          {body.people.length > 0 && (
            <div>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">New people</h3>
              {body.people.map((p, i) => (
                <label key={i} className="flex items-center gap-2 py-1 text-sm text-ink-2">
                  <input type="checkbox" checked={p.include} onChange={(e) => set("people", i, { include: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
                  {p.name} {p.relation && <span className="text-ink-3">({p.relation})</span>}
                </label>
              ))}
            </div>
          )}
          {body.media.length > 0 && (
            <div>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Watched / read</h3>
              {body.media.map((m, i) => (
                <label key={i} className="flex items-center gap-2 py-1 text-sm text-ink-2">
                  <input type="checkbox" checked={m.include} onChange={(e) => set("media", i, { include: e.target.checked })} className="h-4 w-4 accent-[var(--accent)]" />
                  {m.title} <span className="text-ink-3">· {m.kind}</span>
                </label>
              ))}
            </div>
          )}
        </section>
      )}

      {allowJournal && (
        <Toggle checked={appendJournal} onChange={setAppendJournal} label="Also add the original text to this day's journal (and its file)" />
      )}
      <ErrorNote error={commit.error} />
      <div className="flex items-center justify-between gap-2">
        <p className="text-[12px] text-ink-3">Drafted by {draft.model ?? "the model"} — nothing is saved until you press Save.</p>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => discard.mutate()} loading={discard.isPending}>
            Discard
          </Button>
          <Button variant="primary" icon={<Check size={16} />} onClick={() => commit.mutate()} loading={commit.isPending}>
            Save selected
          </Button>
        </div>
      </div>
    </div>
  );
}

type CaptureTab = "ai" | "quick" | "one";

export function CaptureModal({
  open,
  onClose,
  initialText,
  initialDate,
  initialTab = "ai",
  fromJournal = false,
}: {
  open: boolean;
  onClose: () => void;
  initialText?: string;
  initialDate?: string;
  initialTab?: CaptureTab;
  /** The text is a journal page already: no "add it to the journal" option. */
  fromJournal?: boolean;
}) {
  const today = useToday();
  const [tab, setTab] = useState<CaptureTab>(initialTab);
  const [text, setText] = useState("");
  const [date, setDate] = useState(today);
  const [draft, setDraft] = useState<CaptureDraft | null>(null);
  const [preview, setPreview] = useState<QuickResult | null>(null);
  const invalidate = useInvalidateLedger();
  const toast = useToast();
  const { data: profile } = useProfile();

  useEffect(() => {
    if (open) {
      setText(initialText ?? "");
      setDate(initialDate ?? today);
      setDraft(null);
      setPreview(null);
      setTab(initialTab);
    }
  }, [open, initialText, initialDate, initialTab, today]);

  const extract = useMutation({
    mutationFn: () => api.post<CaptureDraft>("/api/ai/capture", { text, date }),
    onSuccess: setDraft,
  });
  const quick = useMutation({
    mutationFn: (commit: boolean) => api.post<QuickResult>("/api/time/quick", { text, date, commit }),
    onSuccess: (r) => {
      setPreview(r);
      if (r.committed) {
        invalidate();
        toast(`Logged ${r.time_entries.length} entries, ${r.transactions.length} transactions`, "good");
        onClose();
      }
    },
  });

  return (
    <Modal open={open} onClose={onClose} title="Capture" wide>
      {draft ? (
        <CaptureReview draft={draft} onDone={onClose} allowJournal={!fromJournal} />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { id: "ai", label: <span className="inline-flex items-center gap-1.5"><Sparkles size={14} /> Tell the assistant</span> },
                { id: "quick", label: <span className="inline-flex items-center gap-1.5"><Wand2 size={14} /> Quick log</span> },
                { id: "one", label: <span className="inline-flex items-center gap-1.5"><Plus size={14} /> One entry</span> },
              ]}
            />
            {tab !== "one" && <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-44" />}
          </div>
          {tab === "one" ? (
            <EntryForm key={`one-${date}`} defaultDay={date} onDone={onClose} />
          ) : tab === "ai" ? (
            <>
              <Textarea
                rows={9}
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder={"Write the day the way you write the Virtual Memory:\n\nWoke at 05:40 for Fajr, back at 06:10. Linear algebra until 08:30. Taxi to the office for 400 FCFA, worked on the assistant 10:00–13:00. After Asr, 1h of reactions on YouTube (ReactionHub)… Bought beans for 50."}
              />
              <p className="text-[12px] text-ink-3">
                The model proposes records; you check them before anything is saved, and a saved capture can be undone.
                {profile?.ai_settings?.mode === "local" ? " Local model: private, slower." : " Uses one free-model request."}
              </p>
              <ErrorNote error={extract.error} />
              <div className="flex justify-end">
                <Button variant="primary" icon={<Sparkles size={15} />} disabled={text.trim().length < 3} loading={extract.isPending} onClick={() => extract.mutate()}>
                  {extract.isPending ? "Reading…" : "Draft records"}
                </Button>
              </div>
            </>
          ) : (
            <>
              <Textarea
                rows={8}
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                  setPreview(null);
                }}
                className="font-mono text-[13px]"
                placeholder={"05:40-06:10 Fajr #prayer\n09:00-11:30 Linear algebra #math @Ibrahim !Office\n23:10-01:40 reactions #reaction\n-400 taxi #transport\n+1000 gift from uncle #gift\n!! Workout done"}
              />
              <p className="text-[12px] text-ink-3">
                <code>HH:MM-HH:MM title #category @person !place</code> · <code>-400 item #category</code> · <code>!! habit done</code>. No AI, no internet.
              </p>
              {preview && (
                <div className="space-y-1 rounded-xl border border-line p-3 text-[13px]">
                  {preview.time_entries.map((t, i) => (
                    <p key={i} className="text-ink-2">
                      <span className="num text-ink">{t.start}–{t.end}</span> {t.title} <span className="text-ink-3">→ {t.category ?? "uncategorised"}</span>
                    </p>
                  ))}
                  {preview.transactions.map((t, i) => (
                    <p key={`t${i}`} className="text-ink-2">
                      <span className="num text-ink">{t.direction === "in" ? "+" : "−"}{t.amount}</span> {t.item} <span className="text-ink-3">#{t.category}</span>
                    </p>
                  ))}
                  {preview.habit_logs.map((h, i) => (
                    <p key={`h${i}`} className="text-ink-2">{h.habit}: {h.status}</p>
                  ))}
                  {preview.errors.map((e) => (
                    <p key={e} className="text-critical">{e}</p>
                  ))}
                </div>
              )}
              <ErrorNote error={quick.error} />
              <div className="flex justify-end gap-2">
                <Button onClick={() => quick.mutate(false)} loading={quick.isPending && !quick.variables} disabled={!text.trim()}>
                  Preview
                </Button>
                <Button variant="primary" icon={<Check size={15} />} onClick={() => quick.mutate(true)} disabled={!text.trim()} loading={quick.isPending && !!quick.variables}>
                  Log it
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
