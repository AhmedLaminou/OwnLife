import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Lock, Pencil, Plus, Sunrise, Trash2 } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { HBars } from "../components/charts";
import { LifeGrid } from "../components/LifeGrid";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, StatTile, Textarea, Toggle } from "../components/ui";
import { api } from "../lib/api";
import { compactNum, longDate, num } from "../lib/format";
import { useProfile } from "../lib/hooks";
import { KIND_LABEL, chartKinds, kindColor } from "../lib/kinds";
import type { Chapter, EventArea, Kind, LifeEvent, LifeOverview, WeekCell } from "../lib/types";

const HOURS_PER_YEAR = 24 * 365.2425;

function useLife() {
  const overview = useQuery({ queryKey: ["life", "overview"], queryFn: () => api.get<LifeOverview>("/api/life/overview") });
  const weeks = useQuery({ queryKey: ["life", "weeks"], queryFn: () => api.get<{ weeks: Record<string, WeekCell> }>("/api/life/weeks") });
  return { overview, weeks };
}

function ChapterEditor({ open, onClose, chapters }: { open: boolean; onClose: () => void; chapters: Chapter[] }) {
  const qc = useQueryClient();
  const blank = { title: "", start_date: "", end_date: "", kind: "past" as "past" | "plan", description: "", approximate: false };
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [form, setForm] = useState(blank);
  const done = () => {
    qc.invalidateQueries({ queryKey: ["life"] });
    setEditing(null);
  };
  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, end_date: form.end_date || null, description: form.description || null };
      return editing === "new" ? api.post("/api/chapters", body) : api.patch(`/api/chapters/${editing}`, body);
    },
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/chapters/${id}`), onSuccess: done });
  return (
    <Modal open={open} onClose={onClose} title="Life chapters" wide>
      {editing === null ? (
        <div className="space-y-2">
          {chapters.map((c) => (
            <div key={c.id} className="flex items-center gap-3 rounded-xl border border-line px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{c.title}</p>
                <p className="text-[12px] text-ink-3">
                  {c.start_date} → {c.end_date ?? "ongoing"} {c.approximate && "· approximate"}
                </p>
              </div>
              <Badge tone={c.kind === "plan" ? "amber" : "neutral"}>{c.kind}</Badge>
              <Button size="sm" variant="ghost" icon={<Pencil size={14} />} onClick={() => {
                setForm({ title: c.title, start_date: c.start_date, end_date: c.end_date ?? "", kind: c.kind, description: c.description ?? "", approximate: c.approximate });
                setEditing(c.id);
              }} aria-label="Edit" />
              <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => remove.mutate(c.id)} aria-label="Delete" />
            </div>
          ))}
          <Button icon={<Plus size={15} />} onClick={() => { setForm(blank); setEditing("new"); }}>
            Add a chapter
          </Button>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <Field label="Title"><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required /></Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Start"><Input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} required /></Field>
            <Field label="End" hint="empty = ongoing"><Input type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} /></Field>
            <Field label="Kind">
              <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as "past" | "plan" })}>
                <option value="past">Lived</option>
                <option value="plan">Plan</option>
              </Select>
            </Field>
          </div>
          <Field label="Description"><Textarea rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
          <Toggle checked={form.approximate} onChange={(v) => setForm({ ...form, approximate: v })} label="The dates are approximate" />
          <ErrorNote error={save.error} />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Back</Button>
            <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

const AREA_LABEL: Record<EventArea, string> = {
  education: "Education",
  family: "Family",
  faith: "Faith",
  health: "Health",
  work: "Work",
  move: "Move",
  travel: "Travel",
  achievement: "Achievement",
  turning_point: "Turning point",
  other: "Other",
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "12 Dec 2005", "Sep 2022" or "2022": only as precise as what is known. */
function eventDate(e: LifeEvent): string {
  const [y, m, d] = e.date.split("-").map(Number);
  if (e.precision === "year") return String(y);
  if (e.precision === "month") return `${MONTHS[m - 1]} ${y}`;
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

function EventEditor({ open, onClose, event }: { open: boolean; onClose: () => void; event: LifeEvent | null }) {
  const qc = useQueryClient();
  const blank = { date: "", precision: "day" as LifeEvent["precision"], title: "", description: "", area: "other" as EventArea, importance: 2, is_private: false };
  const [f, setF] = useState(blank);
  useEffect(() => {
    if (open) {
      setF(event ? { date: event.date, precision: event.precision, title: event.title, description: event.description ?? "", area: event.area, importance: event.importance, is_private: event.is_private } : blank);
    }
  }, [open, event]); // eslint-disable-line react-hooks/exhaustive-deps
  const done = () => {
    qc.invalidateQueries({ queryKey: ["life"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, description: f.description || null };
      return event ? api.patch(`/api/life/events/${event.id}`, body) : api.post("/api/life/events", body);
    },
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => api.del(`/api/life/events/${event!.id}`), onSuccess: done });
  return (
    <Modal open={open} onClose={onClose} title={event ? "Edit a moment" : "Add a moment"}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="What happened"><Input autoFocus value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Graduated — with honours" required /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="When"><Input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} required /></Field>
          <Field label="Known to the">
            <Select value={f.precision} onChange={(e) => setF({ ...f, precision: e.target.value as LifeEvent["precision"] })}>
              <option value="day">Day</option>
              <option value="month">Month (the day is a guess)</option>
              <option value="year">Year (the month is a guess)</option>
            </Select>
          </Field>
          <Field label="Area">
            <Select value={f.area} onChange={(e) => setF({ ...f, area: e.target.value as EventArea })}>
              {(Object.keys(AREA_LABEL) as EventArea[]).map((a) => <option key={a} value={a}>{AREA_LABEL[a]}</option>)}
            </Select>
          </Field>
          <Field label="Weight">
            <Select value={f.importance} onChange={(e) => setF({ ...f, importance: Number(e.target.value) })}>
              <option value={1}>A detail</option>
              <option value={2}>Notable</option>
              <option value={3}>Defining</option>
            </Select>
          </Field>
        </div>
        <Field label="What it meant"><Textarea rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Toggle checked={f.is_private} onChange={(v) => setF({ ...f, is_private: v })} label="Private — blurred on screen, never sent to a cloud model" />
        <ErrorNote error={save.error ?? remove.error} />
        <div className="flex justify-between">
          {event ? <Button type="button" variant="ghost" icon={<Trash2 size={15} />} onClick={() => remove.mutate()}>Delete</Button> : <span />}
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}

/** The past in points: chapters are eras, these are the moments that turned them. */
function LifeEvents({ events, birth }: { events: LifeEvent[]; birth: string }) {
  const [editing, setEditing] = useState<LifeEvent | null | "new">(null);
  const [revealed, setRevealed] = useState(false);
  const byYear = useMemo(() => {
    const map = new Map<string, LifeEvent[]>();
    for (const e of events) map.set(e.date.slice(0, 4), [...(map.get(e.date.slice(0, 4)) ?? []), e]);
    return [...map.entries()];
  }, [events]);
  const ageAt = (date: string) => Math.floor((Date.parse(date) - Date.parse(birth)) / (365.2425 * 86400000));
  return (
    <Card
      title="Moments"
      subtitle="Dated events of your life, ringed in amber on the grid above"
      action={
        <>
          {events.some((e) => e.is_private) && (
            <Button size="sm" variant="ghost" icon={<Lock size={14} />} onClick={() => setRevealed((r) => !r)}>{revealed ? "Hide private" : "Show private"}</Button>
          )}
          <Button size="sm" icon={<Plus size={14} />} onClick={() => setEditing("new")}>Add a moment</Button>
        </>
      }
    >
      {events.length ? (
        <ol className="relative space-y-5 border-l border-line pl-5">
          {byYear.map(([year, items]) => (
            <li key={year}>
              <p className="-ml-5 mb-2 flex items-center gap-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">
                <span className="h-2 w-2 -translate-x-[4.5px] rounded-full bg-ink-3" />
                {year} · age {ageAt(`${year}-12-31`)}
              </p>
              <ul className="space-y-2">
                {items.map((e) => (
                  <li key={e.id}>
                    <button onClick={() => setEditing(e)} className="group flex w-full items-start gap-3 rounded-xl px-2 py-1.5 text-left transition hover:bg-panel-hover">
                      <span
                        className="mt-1.5 shrink-0 rotate-45 rounded-[2px] bg-amber"
                        style={{ width: 4 + e.importance * 2, height: 4 + e.importance * 2 }}
                        aria-hidden
                      />
                      <span className={clsx("min-w-0 flex-1", e.is_private && "private-blur")} data-revealed={revealed}>
                        <span className="block text-[14px] font-medium text-ink">{e.title}</span>
                        <span className="block text-[12px] text-ink-3">
                          {eventDate(e)} · {AREA_LABEL[e.area]}{e.description ? ` — ${e.description}` : ""}
                        </span>
                      </span>
                      <Pencil size={13} className="mt-1 shrink-0 text-ink-3 opacity-0 transition group-hover:opacity-100" />
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      ) : (
        <Empty title="No moments yet">Add the dates that shaped you — an exam, a move, a first. Settings → Data → Personal seed adds the ones already known.</Empty>
      )}
      <EventEditor open={editing !== null} onClose={() => setEditing(null)} event={editing === "new" ? null : editing} />
    </Card>
  );
}

/** The essay's lifetime budget, made interactive: hours a day → years of what is left. */
function BudgetCalculator({ daysLeft, sleep }: { daysLeft: number; sleep: number }) {
  const [rows, setRows] = useState([
    { key: "sleep", label: "Sleep", hours: sleep, kind: "maintenance" as Kind },
    { key: "upkeep", label: "Meals, hygiene, transport", hours: 3.5, kind: "maintenance" as Kind },
    { key: "work", label: "Work / career", hours: 5.7, kind: "work" as Kind },
    { key: "learning", label: "Deliberate learning", hours: 3, kind: "core" as Kind },
    { key: "body", label: "Exercise", hours: 1, kind: "body" as Kind },
    { key: "social", label: "Relationships", hours: 2, kind: "social" as Kind },
    { key: "noise", label: "Entertainment / noise", hours: 2, kind: "noise" as Kind },
  ]);
  const used = rows.reduce((a, r) => a + r.hours, 0);
  const free = 24 - used;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="space-y-4">
        {rows.map((r, i) => (
          <div key={r.key}>
            <div className="mb-1 flex items-center justify-between text-[13px]">
              <span className="inline-flex items-center gap-2 text-ink-2">
                <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: kindColor(r.kind) }} />
                {r.label}
              </span>
              <span className="num font-medium text-ink">{r.hours.toFixed(1)} h/day</span>
            </div>
            <input
              type="range"
              min={0}
              max={14}
              step={0.25}
              value={r.hours}
              onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, hours: Number(e.target.value) } : x)))}
              className="w-full accent-[var(--accent)]"
              aria-label={`${r.label} hours per day`}
            />
          </div>
        ))}
        <p className={free < 0 ? "text-[13px] text-critical" : "text-[13px] text-ink-3"}>
          {free < 0 ? `Over by ${(-free).toFixed(1)}h — a day has 24.` : `${free.toFixed(1)}h a day left unallocated.`}
        </p>
      </div>
      <HBars
        rows={rows.map((r) => ({
          key: r.key,
          label: r.label,
          value: (r.hours * daysLeft) / HOURS_PER_YEAR,
          color: kindColor(r.kind),
          sub: `${compactNum(r.hours * daysLeft)} hours`,
        }))}
        format={(v) => `${v.toFixed(1)} yrs`}
      />
    </div>
  );
}

export function LifePage() {
  const { overview, weeks } = useLife();
  const { data: profile } = useProfile();
  const [chapters, setChapters] = useState(false);
  const o = overview.data;
  const [noiseTarget, setNoiseTarget] = useState<number | null>(null);

  const projections = useMemo(() => (o?.projections ?? []).filter((p) => p.kind !== "uncategorized"), [o]);
  if (overview.isLoading || !o) return <div className="grid h-64 place-items-center"><Spinner /></div>;
  if (!o.configured) {
    return (
      <Card>
        <Empty title="Set your birth date to see your life in weeks">Settings → Profile.</Empty>
      </Card>
    );
  }

  const noiseAvg = (o.measured?.avg_hours_by_kind.noise ?? 0) + (o.measured?.avg_hours_by_kind.destructive ?? 0);
  const coreAvg = o.measured?.avg_hours_by_kind.core ?? 0;
  const target = noiseTarget ?? Math.min(noiseAvg, profile?.noise_budget_hours ?? 1);
  const reclaimed = Math.max(0, noiseAvg - target);
  const days60 = o.days_to_60 ?? 0;
  const merged = chartKinds(Object.fromEntries(projections.map((p) => [p.kind, p.continuous_years_until_60])));

  return (
    <div className="space-y-5">
      <PageHeader
        title="The scale of a life"
        subtitle={`Born ${longDate(o.birth_date!)} · horizon ${o.life_expectancy_years} years · ${o.weeks_alive?.toLocaleString("en")} weeks lived`}
        actions={<Button icon={<Pencil size={15} />} onClick={() => setChapters(true)}>Chapters</Button>}
      />

      <div className="grid gap-4 md:grid-cols-[1.2fr_repeat(4,1fr)]">
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="glass rounded-2xl p-5">
          <p className="text-[13px] text-ink-3">Weeks left</p>
          <p className="mt-1 text-[52px] font-semibold leading-none tracking-tight">{num(o.weeks_left)}</p>
          <p className="mt-2 text-[12px] text-ink-3">of {num(o.total_weeks)} · next birthday in {o.days_to_next_birthday} days</p>
        </motion.div>
        <StatTile label="Age" value={o.age_years!.toFixed(2)} sub={`${num(o.days_alive)} days alive`} />
        <StatTile label="Waking hours left" value={compactNum(o.waking_hours_left!)} sub={`at ${o.sleep_hours_per_day}h of sleep`} />
        <StatTile label="Discretionary hours" value={compactNum(o.discretionary_hours_left!)} sub="after sleep and upkeep" />
        <StatTile label="Asleep, still to come" value={`${o.sleep_years_left} yrs`} sub="the giant elephant" />
      </div>

      <Card title="A life in weeks" subtitle="One column per year of age · hover a week · click a chapter">
        {weeks.data ? (
          <LifeGrid
            birth={o.birth_date!}
            years={o.life_expectancy_years!}
            today={o.today}
            awakening={o.awakening?.date}
            chapters={o.chapters}
            events={o.events}
            weeks={weeks.data.weeks}
          />
        ) : (
          <Spinner />
        )}
      </Card>

      <LifeEvents events={o.events} birth={o.birth_date!} />

      <div className="grid gap-5 xl:grid-cols-2">
        <Card
          title="Where the last 30 days lead"
          subtitle={`Averages over ${o.measured?.tracked_days ?? 0} tracked days, carried to age 60 — in continuous years (24h a day)`}
        >
          {projections.length ? (
            <HBars
              rows={(Object.keys(merged) as (keyof typeof merged)[])
                .filter((k) => merged[k] > 0)
                .map((k) => ({
                  key: k,
                  label: KIND_LABEL[k],
                  value: merged[k],
                  color: kindColor(k),
                  sub: `${(projections.find((p) => p.kind === k)?.avg_hours_per_day ?? 0).toFixed(1)} h/day now`,
                }))}
              format={(v) => `${v.toFixed(2)} yrs`}
            />
          ) : (
            <Empty title="Not enough data yet">Log at least two hours on a few days; projections start from real days, not wishes.</Empty>
          )}
        </Card>

        <Card title="What if" subtitle="Move the noise slider: the hours do not disappear, they move">
          <div className="space-y-5">
            <div>
              <div className="mb-1 flex justify-between text-[13px]">
                <span className="text-ink-2">Noise per day</span>
                <span className="num font-medium">{target.toFixed(2)} h (now {noiseAvg.toFixed(2)} h)</span>
              </div>
              <input
                type="range"
                min={0}
                max={Math.max(noiseAvg, 1)}
                step={0.05}
                value={target}
                onChange={(e) => setNoiseTarget(Number(e.target.value))}
                className="w-full accent-[var(--accent)]"
                aria-label="Noise hours per day"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <StatTile label="Reclaimed by 60" value={`${((reclaimed * days60) / HOURS_PER_YEAR).toFixed(2)} yrs`} sub={`${compactNum(reclaimed * days60)} hours`} />
              <StatTile label="Core by 60, if redirected" value={`${(((coreAvg + reclaimed) * days60) / HOURS_PER_YEAR).toFixed(2)} yrs`} sub={`now on track for ${((coreAvg * days60) / HOURS_PER_YEAR).toFixed(2)}`} />
            </div>
            <p className="text-[13px] leading-relaxed text-ink-3">
              Three hours a day from 21 to 60 is about 4.9 continuous years of intellectual work. You do not need every minute — you need to stop
              systematically throwing thousands of hours away.
            </p>
          </div>
        </Card>
      </div>

      {o.awakening && (
        <Card title="Since the Awakening" subtitle={`${longDate(o.awakening.date)} — Day ${o.awakening.day_number}`}>
          <div className="grid gap-4 md:grid-cols-[1fr_2fr]">
            <div className="space-y-3">
              <p className="flex items-center gap-2 text-sm text-ink-2">
                <Sunrise size={16} className="text-amber" /> {(o.awakening.share_of_life_before * 100).toFixed(1)}% of your life so far came before it.
              </p>
              <p className="text-sm text-ink-3">
                You were {o.awakening.age_at_awakening.toFixed(1)}. It has been {o.awakening.days_since} days — {o.awakening.weeks_since} weeks.
              </p>
            </div>
            <HBars
              rows={Object.entries(chartKinds(Object.fromEntries(Object.entries(o.awakening.hours_by_kind).map(([k, v]) => [k, v ?? 0]))))
                .filter(([, v]) => v > 0)
                .map(([k, v]) => ({ key: k, label: KIND_LABEL[k as Kind], value: v, color: kindColor(k) }))}
              format={(v) => `${v.toFixed(1)} h`}
            />
          </div>
        </Card>
      )}

      <Card title="The lifetime budget" subtitle="Hours a day, turned into years of what is left">
        <BudgetCalculator daysLeft={o.days_left!} sleep={o.sleep_hours_per_day!} />
      </Card>

      <ChapterEditor open={chapters} onClose={() => setChapters(false)} chapters={o.chapters} />
    </div>
  );
}
