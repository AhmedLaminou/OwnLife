import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Users } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Spinner, Textarea } from "../components/ui";
import { api } from "../lib/api";
import { clock, hm, longDate, localDate, num } from "../lib/format";
import { useTimeZone } from "../lib/hooks";
import type { Person, TimeEntry } from "../lib/types";

function initials(name: string): string {
  return name.split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
}

function PersonModal({ id, onClose }: { id: number | null; onClose: () => void }) {
  const tz = useTimeZone();
  const qc = useQueryClient();
  const { data: p } = useQuery({ queryKey: ["people", id], queryFn: () => api.get<Person>(`/api/people/${id}`), enabled: id !== null });
  const [notes, setNotes] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => api.patch(`/api/people/${id}`, { notes }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["people"] }),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/people/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["people"] });
      onClose();
    },
  });
  const entries = (p?.entries as TimeEntry[] | undefined) ?? [];
  return (
    <Modal open={id !== null} onClose={onClose} title={p?.name ?? "…"} wide>
      {!p ? (
        <Spinner />
      ) : (
        <div className="grid gap-5 md:grid-cols-[1fr_1.3fr]">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {p.relation && <Badge tone="accent">{p.relation}</Badge>}
              <Badge>{num(p.hours_together, 1)} h together</Badge>
            </div>
            <Field label="Notes">
              <Textarea rows={6} value={notes ?? p.notes ?? ""} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <div className="flex justify-between">
              <Button variant="ghost" icon={<Trash2 size={14} />} onClick={() => confirm(`Remove ${p.name}?`) && remove.mutate()}>Remove</Button>
              <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={notes === null}>Save notes</Button>
            </div>
          </div>
          <div>
            <p className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Time together</p>
            {entries.length ? (
              <ul className="scroll-thin max-h-[420px] divide-y divide-line overflow-y-auto">
                {entries.map((e) => (
                  <li key={e.id} className="py-2 text-[13px]">
                    <p className="text-ink">{e.title}</p>
                    <p className="text-ink-3">
                      {longDate(localDate(new Date(e.started_at), tz))} · {clock(e.started_at, tz)} · {hm(e.duration_seconds)}
                      {e.location && ` · ${e.location}`}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-ink-3">No logged time with {p.name} yet. Add “with {p.name}” when logging, or let Capture pick it up.</p>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

export function PeoplePage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["people"], queryFn: () => api.get<Person[]>("/api/people") });
  const [open, setOpen] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", relation: "" });
  const create = useMutation({
    mutationFn: () => api.post("/api/people", { name: f.name, relation: f.relation || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["people"] });
      setAdding(false);
      setF({ name: "", relation: "" });
    },
  });
  const sorted = [...(data ?? [])].sort((a, b) => (b.hours_together ?? 0) - (a.hours_together ?? 0) || a.name.localeCompare(b.name));
  return (
    <div>
      <PageHeader
        title="People"
        subtitle="Who the hours are spent with. Your universe, your filter."
        actions={<Button variant="primary" icon={<Plus size={15} />} onClick={() => setAdding(true)}>Person</Button>}
      />
      {isLoading ? (
        <Spinner />
      ) : sorted.length ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {sorted.map((p, i) => (
            <motion.button
              key={p.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.02, 0.3) }}
              onClick={() => setOpen(p.id)}
              className="glass flex items-center gap-3 rounded-2xl p-4 text-left transition hover:bg-panel-hover"
            >
              <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-sm font-semibold text-accent">{initials(p.name)}</span>
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium text-ink">{p.name}</p>
                <p className="truncate text-[12px] text-ink-3">
                  {p.relation ?? "—"} · {num(p.hours_together, 1)} h
                  {p.last_seen && ` · last ${longDate(p.last_seen.slice(0, 10))}`}
                </p>
              </div>
            </motion.button>
          ))}
        </div>
      ) : (
        <Card><Empty icon={<Users size={24} />} title="No one yet" /></Card>
      )}
      <PersonModal id={open} onClose={() => setOpen(null)} />
      <Modal open={adding} onClose={() => setAdding(false)} title="Add a person">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <Field label="Name"><Input autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
          <Field label="Relation"><Input value={f.relation} onChange={(e) => setF({ ...f, relation: e.target.value })} placeholder="family, friend, colleague…" /></Field>
          <ErrorNote error={create.error} />
          <div className="flex justify-end"><Button type="submit" variant="primary" loading={create.isPending}>Add</Button></div>
        </form>
      </Modal>
    </div>
  );
}
