import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleSlash, Eye, EyeOff, Flame, Plus, ShieldCheck, ShieldX, Trash2 } from "lucide-react";
import { useState } from "react";
import { Columns, HeatCalendar } from "../components/charts";
import { useInvalidateLedger } from "../components/domain";
import { NamedIcon } from "../components/icons";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, Textarea, Toggle, useToast } from "../components/ui";
import { api } from "../lib/api";
import { CHART_KINDS, KIND_LABEL } from "../lib/kinds";
import type { Habit } from "../lib/types";

function NewHabit({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ name: "", kind: "build" as "build" | "quit", description: "", is_private: false, auto: "none", ruleKind: "core", hours: 4, target_per_week: 7 });
  const save = useMutation({
    mutationFn: () =>
      api.post("/api/habits", {
        name: f.name,
        kind: f.kind,
        description: f.description || null,
        is_private: f.is_private,
        target_per_week: f.target_per_week,
        rule: f.kind === "quit" || f.auto === "none" ? null : f.auto === "journal_written" ? { type: "journal_written" } : { type: f.auto, kind: f.ruleKind, hours: f.hours },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["habits"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="New habit">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><Input autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required placeholder="Wake at Fajr" /></Field>
        <Field label="Type">
          <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as "build" | "quit" })}>
            <option value="build">Build — something to do</option>
            <option value="quit">Quit — count clean days, log urges and relapses</option>
          </Select>
        </Field>
        {f.kind === "build" && (
          <Field label="Evaluated">
            <Select value={f.auto} onChange={(e) => setF({ ...f, auto: e.target.value })}>
              <option value="none">By hand (I tick it)</option>
              <option value="kind_hours_min">From the ledger: at least N hours of a kind</option>
              <option value="kind_hours_max">From the ledger: at most N hours of a kind</option>
              <option value="journal_written">When the journal is written</option>
            </Select>
          </Field>
        )}
        {f.kind === "build" && (f.auto === "kind_hours_min" || f.auto === "kind_hours_max") && (
          <div className="grid grid-cols-2 gap-3">
            <Field label="Kind">
              <Select value={f.ruleKind} onChange={(e) => setF({ ...f, ruleKind: e.target.value })}>
                {CHART_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
              </Select>
            </Field>
            <Field label="Hours"><Input type="number" min={0} max={24} step={0.5} value={f.hours} onChange={(e) => setF({ ...f, hours: Number(e.target.value) })} /></Field>
          </div>
        )}
        <Field label="Description"><Textarea rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Toggle checked={f.is_private} onChange={(v) => setF({ ...f, is_private: v })} label="Private — blurred on screen until revealed" />
        <ErrorNote error={save.error} />
        <div className="flex justify-end"><Button type="submit" variant="primary" loading={save.isPending}>Create</Button></div>
      </form>
    </Modal>
  );
}

function HabitCard({ h }: { h: Habit }) {
  const [revealed, setRevealed] = useState(!h.is_private);
  const [relapse, setRelapse] = useState(false);
  const [time, setTime] = useState("");
  const [note, setNote] = useState("");
  const invalidate = useInvalidateLedger();
  const toast = useToast();
  const qc = useQueryClient();
  const log = useMutation({
    mutationFn: (body: { status: string; time?: string; note?: string }) => api.post(`/api/habits/${h.id}/log`, body),
    onSuccess: (_d, body) => {
      invalidate();
      qc.invalidateQueries({ queryKey: ["habits"] });
      if (body.status === "urge") toast("Urge resisted. That is the skill being trained.", "good");
      if (body.status === "relapse") toast("Logged. The streak restarts — the data stays honest.", "neutral");
      setRelapse(false);
    },
  });
  const archive = useMutation({
    mutationFn: () => api.patch(`/api/habits/${h.id}`, { archived: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["habits"] }),
  });
  const s = h.stats;
  const isQuit = h.kind === "quit";
  return (
    <Card
      title={
        <span className="inline-flex items-center gap-2">
          <NamedIcon name={h.icon} size={16} className="text-ink-3" />
          <span className={!revealed ? "private-blur" : undefined}>{h.name}</span>
        </span>
      }
      subtitle={h.rule ? "evaluated from your data" : isQuit ? "days since the last relapse" : "ticked by hand"}
      action={
        <>
          {h.is_private && (
            <Button size="sm" variant="ghost" onClick={() => setRevealed((r) => !r)} icon={revealed ? <EyeOff size={14} /> : <Eye size={14} />} aria-label="Reveal" />
          )}
          <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => confirm("Archive this habit?") && archive.mutate()} aria-label="Archive" />
        </>
      }
    >
      <div className={!revealed ? "private-blur" : undefined}>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <div>
            <p className="text-[40px] font-semibold leading-none tracking-tight">{s.current_streak}</p>
            <p className="mt-1 text-[12px] text-ink-3">{isQuit ? "clean days" : "day streak"}</p>
          </div>
          <div className="pb-1 text-[13px] text-ink-3">
            best {s.best_streak}
            {isQuit ? (
              <>
                {" "}· {s.relapses_total} relapse{s.relapses_total === 1 ? "" : "s"} · {s.urges_resisted_total} urge{s.urges_resisted_total === 1 ? "" : "s"} resisted
                {s.next_milestone ? <> · next milestone {s.next_milestone} days (in {s.days_to_next_milestone})</> : null}
              </>
            ) : (
              s.rate_last_30 !== null && s.rate_last_30 !== undefined && <> · {Math.round(s.rate_last_30 * 100)}% of judged days in the last 30</>
            )}
          </div>
        </div>
        <div className="mt-4 overflow-x-auto scroll-thin">
          <HeatCalendar days={s.calendar} />
        </div>
        {isQuit && s.relapse_hours && s.relapse_hours.some((v) => v > 0) && (
          <div className="mt-5">
            <p className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">When relapses happen</p>
            <Columns
              title="Relapses by hour"
              data={s.relapse_hours.map((v, i) => ({ label: i % 3 === 0 ? `${i}h` : "", value: v, tip: `${String(i).padStart(2, "0")}:00–${String(i + 1).padStart(2, "0")}:00` }))}
              format={(v) => String(Math.round(v))}
              color="var(--critical)"
              height={120}
            />
          </div>
        )}
        <div className="mt-5 flex flex-wrap gap-2">
          {isQuit ? (
            <>
              <Button variant="primary" icon={<ShieldCheck size={15} />} onClick={() => log.mutate({ status: "urge" })} loading={log.isPending && log.variables?.status === "urge"}>
                I resisted an urge
              </Button>
              <Button variant="ghost" icon={<ShieldX size={15} />} onClick={() => setRelapse(true)}>Log a relapse</Button>
            </>
          ) : !h.rule ? (
            <>
              <Button variant="primary" icon={<Check size={15} />} onClick={() => log.mutate({ status: "done" })} disabled={s.today === "done"}>
                {s.today === "done" ? "Done today" : "Done"}
              </Button>
              <Button variant="ghost" icon={<CircleSlash size={15} />} onClick={() => log.mutate({ status: "missed" })}>Missed</Button>
            </>
          ) : (
            <Badge tone={s.today === "done" ? "good" : s.today === "missed" ? "critical" : "neutral"}>today: {s.today ?? "—"}</Badge>
          )}
        </div>
        {h.description && <p className="mt-3 text-[12px] text-ink-3">{h.description}</p>}
      </div>
      <Modal open={relapse} onClose={() => setRelapse(false)} title="Log a relapse">
        <div className="space-y-4">
          <p className="text-sm text-ink-3">No judgement here — an honest record is what makes the pattern visible. The time of day matters most.</p>
          <Field label="Around what time?"><Input type="time" value={time} onChange={(e) => setTime(e.target.value)} /></Field>
          <Field label="What led to it? (optional)"><Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Tired, bored, phone in bed…" /></Field>
          <div className="flex justify-end">
            <Button variant="danger" onClick={() => log.mutate({ status: "relapse", time: time || undefined, note: note || undefined })} loading={log.isPending}>Log it</Button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}

export function HabitsPage() {
  const { data, isLoading } = useQuery({ queryKey: ["habits"], queryFn: () => api.get<Habit[]>("/api/habits?days=120") });
  const [creating, setCreating] = useState(false);
  return (
    <div>
      <PageHeader
        title="Habits"
        subtitle="What is built, what is quit. Habits evaluated from the ledger cannot be ticked by wishful thinking."
        actions={<Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>Habit</Button>}
      />
      {isLoading ? (
        <Spinner />
      ) : data?.length ? (
        <div className="grid gap-5 xl:grid-cols-2">
          {data.map((h) => <HabitCard key={h.id} h={h} />)}
        </div>
      ) : (
        <Card><Empty icon={<Flame size={24} />} title="No habits yet" /></Card>
      )}
      <NewHabit open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
