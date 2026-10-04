import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronLeft, ChevronRight, Copy, LayoutTemplate, MoonStar, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { DayTimeline } from "../components/DayTimeline";
import { CategorySelect } from "../components/domain";
import { Button, Card, Empty, ErrorNote, Field, IconButton, Input, Meter, Modal, PageHeader, Select, Spinner, Toggle, useToast } from "../components/ui";
import { api } from "../lib/api";
import { addDays, hm, longDate, minutesInto } from "../lib/format";
import { useNow, useTimeZone, useToday } from "../lib/hooks";
import type { PlanBlock, PlanDay, PlanTemplate, PrayerDay } from "../lib/types";

function BlockModal({ open, onClose, day, block }: { open: boolean; onClose: () => void; day: string; block: PlanBlock | null }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({ title: "", start: "09:00", end: "10:00", category_id: null as number | null, is_fixed: false });
  useEffect(() => {
    if (!open) return;
    setForm(block
      ? { title: block.title, start: block.start, end: block.end, category_id: block.category_id, is_fixed: block.is_fixed }
      : { title: "", start: "09:00", end: "10:00", category_id: null, is_fixed: false });
  }, [open, block]);
  const done = () => {
    qc.invalidateQueries({ queryKey: ["plan"] });
    onClose();
  };
  const save = useMutation({
    mutationFn: () => (block ? api.patch(`/api/plan/blocks/${block.id}`, form) : api.post("/api/plan/blocks", { ...form, date: day })),
    onSuccess: done,
  });
  const remove = useMutation({ mutationFn: () => api.del(`/api/plan/blocks/${block!.id}`), onSuccess: done });
  return (
    <Modal open={open} onClose={onClose} title={block ? "Edit block" : "Plan a block"}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="What"><Input autoFocus value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} required /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Start"><Input type="time" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} required /></Field>
          <Field label="End" hint="earlier than start = after midnight"><Input type="time" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} required /></Field>
        </div>
        <Field label="Category"><CategorySelect value={form.category_id} onChange={(v) => setForm({ ...form, category_id: v })} /></Field>
        <Toggle checked={form.is_fixed} onChange={(v) => setForm({ ...form, is_fixed: v })} label="Fixed (prayer, work hours)" />
        <ErrorNote error={save.error} />
        <div className="flex justify-between">
          {block ? <Button type="button" variant="ghost" icon={<Trash2 size={15} />} onClick={() => remove.mutate()}>Delete</Button> : <span />}
          <Button type="submit" variant="primary" loading={save.isPending}>Save</Button>
        </div>
      </form>
    </Modal>
  );
}

export function PlannerPage() {
  const tz = useTimeZone();
  const today = useToday();
  const now = useNow(60_000);
  const toast = useToast();
  const qc = useQueryClient();
  const [day, setDay] = useState(today);
  const [templateId, setTemplateId] = useState<string>("");
  const [modal, setModal] = useState<{ open: boolean; block: PlanBlock | null }>({ open: false, block: null });
  const plan = useQuery({ queryKey: ["plan", day], queryFn: () => api.get<PlanDay>(`/api/plan/${day}`) });
  const templates = useQuery({ queryKey: ["plan-templates"], queryFn: () => api.get<PlanTemplate[]>("/api/plan-templates") });
  const prayers = useQuery({ queryKey: ["prayer", day], queryFn: () => api.get<PrayerDay>(`/api/prayer/${day}`) });

  // Suggest the template made for this weekday.
  useEffect(() => {
    if (!templates.data?.length) return;
    const weekday = (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
    // The most specific template wins: "Friday — Jumu'ah" over a Monday-to-Saturday one.
    const match =
      templates.data.filter((t) => t.weekdays.includes(weekday)).sort((a, b) => a.weekdays.length - b.weekdays.length)[0] ??
      templates.data[0];
    setTemplateId(String(match.id));
  }, [templates.data, day]);

  const refresh = () => qc.invalidateQueries({ queryKey: ["plan"] });
  const apply = useMutation({
    mutationFn: () =>
      api.post<{ created: number; prayers_aligned: number }>(`/api/plan/${day}/apply-template`, { template_id: Number(templateId), replace: true }),
    onSuccess: (r) => {
      refresh();
      toast(`${r.created} blocks planned${r.prayers_aligned ? ` — ${r.prayers_aligned} prayers moved to today's times` : ""}`, "good");
    },
  });
  const copy = useMutation({
    mutationFn: () => api.post<{ created: number }>(`/api/plan/${day}/copy-from`, { source_date: addDays(day, -1), replace: true }),
    onSuccess: (r) => { refresh(); toast(`${r.created} blocks copied from the day before`, "good"); },
  });
  const saveTemplate = useMutation({
    mutationFn: () => api.post(`/api/plan-templates/from-day/${day}?name=${encodeURIComponent(`Day of ${day}`)}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["plan-templates"] }); toast("Saved as a template", "good"); },
  });

  const isToday = day === today;
  const nowMinute = isToday ? minutesInto(now.toISOString(), day, tz) : null;
  const planBlocks = useMemo(() => (plan.data?.blocks ?? []).map((b) => ({
    id: b.id,
    start: b.start_minute,
    end: b.end_minute,
    title: b.title,
    kind: b.kind,
    sub: `${b.start}–${b.end}${b.is_fixed ? " · fixed" : ""}`,
    dim: nowMinute !== null && b.end_minute < nowMinute && (b.matched_seconds ?? 0) === 0 && !b.is_fixed,
  })), [plan.data, nowMinute]);
  const actualBlocks = useMemo(() => (plan.data?.entries ?? []).map((e) => ({
    id: e.id,
    start: minutesInto(e.started_at, day, tz),
    end: e.ended_at ? minutesInto(e.ended_at, day, tz) : (nowMinute ?? 1440),
    title: e.title,
    kind: e.kind,
    sub: hm(e.duration_seconds),
    running: e.running,
  })), [plan.data, day, tz, nowMinute]);

  return (
    <div>
      <PageHeader
        title="Planner"
        subtitle="The plan next to what happened — adherence counts planned blocks matched by time of the same kind"
        actions={<Button variant="primary" icon={<Plus size={15} />} onClick={() => setModal({ open: true, block: null })}>Block</Button>}
      />
      <div className="mb-5 flex flex-wrap items-center gap-2">
        <IconButton label="Previous day" onClick={() => setDay(addDays(day, -1))}><ChevronLeft size={18} /></IconButton>
        <Input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} className="w-44" />
        <IconButton label="Next day" onClick={() => setDay(addDays(day, 1))}><ChevronRight size={18} /></IconButton>
        <span className="text-sm text-ink-3">{longDate(day)}</span>
        <div className="flex-1" />
        <Select value={templateId} onChange={(e) => setTemplateId(e.target.value)} className="w-56">
          {(templates.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
        <Button icon={<LayoutTemplate size={15} />} onClick={() => apply.mutate()} loading={apply.isPending} disabled={!templateId}>Apply</Button>
        <Button variant="ghost" icon={<Copy size={15} />} onClick={() => copy.mutate()} loading={copy.isPending}>From yesterday</Button>
        <Button variant="ghost" icon={<Save size={15} />} onClick={() => saveTemplate.mutate()} disabled={!plan.data?.blocks.length}>Save as template</Button>
      </div>

      {prayers.data && (
        <div className="mb-5 flex flex-wrap items-center gap-2 text-[13px]">
          <MoonStar size={15} className="text-amber" />
          {(["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"] as const).map((k) => (
            <span key={k} className={clsx("rounded-lg px-2 py-1", k === "sunrise" ? "text-ink-3" : "bg-panel-hover text-ink-2")}>
              {k === "sunrise" ? "sunrise" : k[0].toUpperCase() + k.slice(1)} <span className="num font-medium text-ink">{prayers.data.times[k] ?? "—"}</span>
            </span>
          ))}
          <span className="text-[12px] text-ink-3">
            {prayers.data.city} · {prayers.data.method_name} — computed for this date; offsets in Settings → Rituals
          </span>
        </div>
      )}

      {!plan.data ? (
        <div className="grid h-64 place-items-center"><Spinner /></div>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[1fr_1fr_320px]">
          <Card title="Plan">
            {plan.data.blocks.length ? (
              <DayTimeline blocks={planBlocks} nowMinute={nowMinute} onSelect={(id) => setModal({ open: true, block: plan.data!.blocks.find((b) => b.id === id) ?? null })} height={640} />
            ) : (
              <Empty title="No plan for this day">Apply a template, copy yesterday, or add blocks.</Empty>
            )}
          </Card>
          <Card title="What happened">
            {plan.data.entries.length ? (
              <DayTimeline blocks={actualBlocks} nowMinute={nowMinute} height={640} />
            ) : (
              <Empty title="Nothing logged yet" />
            )}
          </Card>
          <Card title="Adherence">
            {plan.data.adherence !== null ? (
              <>
                <p className="text-4xl font-semibold tracking-tight">{Math.round(plan.data.adherence * 100)}%</p>
                <p className="mb-3 text-[12px] text-ink-3">of planned non-upkeep time matched</p>
                <Meter value={plan.data.adherence} max={1} label="Plan adherence" color={plan.data.adherence >= 0.7 ? "var(--good)" : "var(--k-core)"} />
              </>
            ) : (
              <p className="text-sm text-ink-3">Plan the day to measure it.</p>
            )}
            <ul className="mt-5 space-y-2">
              {plan.data.blocks.filter((b) => b.kind !== "maintenance").map((b) => (
                <li key={b.id} className="text-[13px]">
                  <div className="flex justify-between gap-2">
                    <span className="truncate text-ink-2">{b.start} {b.title}</span>
                    <span className="num text-ink-3">{hm(b.matched_seconds)} / {hm(b.planned_seconds)}</span>
                  </div>
                  <Meter value={b.matched_seconds ?? 0} max={b.planned_seconds ?? 1} label={b.title} height={4} color={`var(--k-${b.kind === "destructive" ? "noise" : b.kind})`} />
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
      <BlockModal open={modal.open} block={modal.block} day={day} onClose={() => setModal({ open: false, block: null })} />
    </div>
  );
}
