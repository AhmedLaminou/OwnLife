import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Gift, Lock, MessageCircle, Plus, Trash2, Unlock, Users } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, Tabs, Textarea, Toggle, useToast } from "../components/ui";
import { api } from "../lib/api";
import { clock, hm, localDate, longDate, money, num, pluralize, shortDate } from "../lib/format";
import { useProfile, useTimeZone, useToday } from "../lib/hooks";
import type { Person, PersonMoment, TimeEntry, Transaction } from "../lib/types";

function initials(name: string): string {
  return name.split(/\s+/).map((p) => p[0]).join("").slice(0, 2).toUpperCase();
}

const KIND_LABEL: Record<PersonMoment["kind"], string> = { gift_from: "gave you", gift_to: "you gave", moment: "moment" };

function MomentsTab({ person, privateDefault }: { person: Person; privateDefault: boolean }) {
  const qc = useQueryClient();
  const today = useToday();
  const [f, setF] = useState({ date: today, kind: "moment" as PersonMoment["kind"], text: "", is_private: privateDefault });
  useEffect(() => setF((x) => ({ ...x, is_private: privateDefault })), [privateDefault]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["people"] });
  const add = useMutation({
    mutationFn: () => api.post(`/api/people/${person.id}/moments`, f),
    onSuccess: () => {
      refresh();
      setF({ ...f, text: "" });
    },
  });
  const patch = useMutation({ mutationFn: ({ id, ...body }: { id: number; is_private: boolean }) => api.patch(`/api/people/moments/${id}`, body), onSuccess: refresh });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/people/moments/${id}`), onSuccess: refresh });
  const moments = (person.moments as PersonMoment[] | undefined) ?? [];
  return (
    <div className="space-y-3">
      <form className="space-y-2 rounded-xl border border-line p-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <div className="grid grid-cols-2 gap-2">
          <Input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} aria-label="Day" />
          <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as PersonMoment["kind"] })} aria-label="Kind">
            <option value="moment">Something happened</option>
            <option value="gift_from">{person.name.split(" ")[0]} gave me</option>
            <option value="gift_to">I gave {person.name.split(" ")[0]}</option>
          </Select>
        </div>
        <div className="flex gap-2">
          <Input value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} placeholder={f.kind === "moment" ? "What they did or said" : "What it was"} required aria-label="What" />
          <Button type="submit" variant="primary" icon={<Plus size={14} />} loading={add.isPending}>Add</Button>
        </div>
        <label className="flex items-center gap-2 text-[12px] text-ink-3">
          <input type="checkbox" checked={f.is_private} onChange={(e) => setF({ ...f, is_private: e.target.checked })} className="h-3.5 w-3.5 accent-[var(--accent)]" />
          Private: stays on this computer, never shown to a cloud model
        </label>
      </form>
      <ErrorNote error={add.error ?? patch.error ?? remove.error} />
      {moments.length ? (
        <ul className="scroll-thin max-h-[360px] divide-y divide-line overflow-y-auto">
          {moments.map((m) => (
            <li key={m.id} className="group flex items-start gap-3 py-2 text-[13px]">
              <span className="mt-0.5 shrink-0 text-ink-3">{m.kind === "moment" ? <MessageCircle size={14} /> : <Gift size={14} className="text-accent" />}</span>
              <div className="min-w-0 flex-1">
                <p className="text-ink">{m.text}</p>
                <p className="text-[12px] text-ink-3">
                  {shortDate(m.date)} · {KIND_LABEL[m.kind]}
                  {m.source === "journal" ? " · from the journal" : m.source === "ai" ? " · by the assistant" : m.source === "capture" ? " · from a capture" : ""}
                </p>
              </div>
              <button onClick={() => patch.mutate({ id: m.id, is_private: !m.is_private })} className={clsx("shrink-0", m.is_private ? "text-accent" : "text-ink-3 opacity-0 group-hover:opacity-100")}
                title={m.is_private ? "Private — click to share with the assistant's cloud model" : "Make it private"} aria-label={m.is_private ? "Private" : "Make private"}>
                {m.is_private ? <Lock size={14} /> : <Unlock size={14} />}
              </button>
              <button onClick={() => remove.mutate(m.id)} className="shrink-0 text-ink-3 opacity-0 hover:text-critical group-hover:opacity-100" aria-label="Delete">
                <Trash2 size={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-ink-3">Nothing yet. Add a gift or a moment here, tell the assistant (“Nadia gave me a book today”), or write it in a capture.</p>
      )}
    </div>
  );
}

function MoneyTab({ person, currency }: { person: Person; currency: string }) {
  const txs = (person.transactions as Transaction[] | undefined) ?? [];
  if (!txs.length) {
    return <p className="text-[13px] text-ink-3">No money between you yet. On the Money page, a transaction “received from” or “given to” {person.name} lands here.</p>;
  }
  return (
    <div>
      <div className="mb-2 flex gap-2">
        <Badge tone="good">received {money(person.money_in ?? 0, currency)}</Badge>
        <Badge>gave {money(person.money_out ?? 0, currency)}</Badge>
      </div>
      <ul className="scroll-thin max-h-[360px] divide-y divide-line overflow-y-auto">
        {txs.map((t) => (
          <li key={t.id} className="flex items-center gap-3 py-2 text-[13px]">
            <span className="w-14 shrink-0 text-[12px] text-ink-3">{shortDate(t.date)}</span>
            <span className="min-w-0 flex-1 truncate text-ink">{t.item}</span>
            <span className={clsx("num font-medium", t.direction === "in" ? "text-good" : "text-ink")}>{t.direction === "in" ? "+" : "−"}{money(t.amount, t.currency)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PersonModal({ id, people, privateDefault, onClose }: { id: number | null; people: Person[]; privateDefault: boolean; onClose: () => void }) {
  const tz = useTimeZone();
  const qc = useQueryClient();
  const toast = useToast();
  const { data: profile } = useProfile();
  const [tab, setTab] = useState<"moments" | "money" | "time">("moments");
  const { data: p } = useQuery({ queryKey: ["people", id], queryFn: () => api.get<Person>(`/api/people/${id}`), enabled: id !== null });
  const [notes, setNotes] = useState<string | null>(null);
  const [into, setInto] = useState<number | null>(null);
  useEffect(() => {
    setNotes(null);
    setInto(null);
  }, [id]);
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
  const merge = useMutation({
    mutationFn: () => api.post<{ into: Person; moved: Record<string, number> }>(`/api/people/${id}/merge`, { into_id: into }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["money"] });
      toast(`Merged into ${r.into.name}`, "good");
      onClose();
    },
  });
  const entries = (p?.entries as TimeEntry[] | undefined) ?? [];
  const others = people.filter((x) => x.id !== id);
  return (
    <Modal open={id !== null} onClose={onClose} title={p?.name ?? "…"} wide>
      {!p ? (
        <Spinner />
      ) : (
        <div className="grid gap-5 md:grid-cols-[1fr_1.4fr]">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              {p.relation && <Badge tone="accent">{p.relation}</Badge>}
              {(p.hours_together ?? 0) > 0 && <Badge>{num(p.hours_together, 1)} h together</Badge>}
              {(p.money_in ?? 0) > 0 && <Badge tone="good">received {money(p.money_in ?? 0, profile?.currency ?? "XOF")}</Badge>}
              {(p.money_out ?? 0) > 0 && <Badge>gave {money(p.money_out ?? 0, profile?.currency ?? "XOF")}</Badge>}
            </div>
            <Field label="Notes">
              <Textarea rows={5} value={notes ?? p.notes ?? ""} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            <div className="flex justify-end">
              <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={notes === null}>Save notes</Button>
            </div>
            {others.length > 0 && (
              <div className="space-y-2 border-t border-line pt-3">
                <p className="text-[12px] text-ink-3">The same person as someone else in the list? Everything moves to them.</p>
                <div className="flex gap-2">
                  <Select value={into ?? ""} onChange={(e) => setInto(e.target.value ? Number(e.target.value) : null)} aria-label="Same person as">
                    <option value="">Same person as…</option>
                    {others.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                  </Select>
                  <Button variant="secondary" disabled={into === null} loading={merge.isPending}
                    onClick={() => confirm(`Move everything of ${p.name} to ${others.find((o) => o.id === into)?.name} and remove ${p.name}?`) && merge.mutate()}>
                    Merge
                  </Button>
                </div>
              </div>
            )}
            <ErrorNote error={save.error ?? merge.error ?? remove.error} />
            <Button variant="ghost" icon={<Trash2 size={14} />} onClick={() => confirm(`Remove ${p.name}? Their gifts and moments go too; time and money stay, unlinked.`) && remove.mutate()}>Remove</Button>
          </div>
          <div>
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: "moments", label: `Moments & gifts${(p.moments as PersonMoment[] | undefined)?.length ? ` · ${(p.moments as PersonMoment[]).length}` : ""}` },
              { id: "money", label: "Money" },
              { id: "time", label: "Time together" },
            ]} />
            <div className="mt-3">
              {tab === "moments" && <MomentsTab person={p} privateDefault={privateDefault} />}
              {tab === "money" && <MoneyTab person={p} currency={profile?.currency ?? "XOF"} />}
              {tab === "time" && (entries.length ? (
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
              ))}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function PeoplePage() {
  const qc = useQueryClient();
  const { data: profile } = useProfile();
  const currency = profile?.currency ?? "XOF";
  const { data, isLoading } = useQuery({ queryKey: ["people"], queryFn: () => api.get<Person[]>("/api/people") });
  const prefs = useQuery({ queryKey: ["people", "prefs"], queryFn: () => api.get<{ moments_private: boolean }>("/api/people/prefs") });
  const setPrefs = useMutation({
    mutationFn: (moments_private: boolean) => api.put("/api/people/prefs", { moments_private }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["people", "prefs"] }),
  });
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
  const last = (p: Person) => p.last_interaction ?? "";
  const sorted = [...(data ?? [])].sort((a, b) => last(b).localeCompare(last(a)) || (b.hours_together ?? 0) - (a.hours_together ?? 0) || a.name.localeCompare(b.name));
  return (
    <div>
      <PageHeader
        title="People"
        subtitle="Who the hours are spent with, what went between you, what is worth remembering."
        actions={
          <>
            {prefs.data && (
              <Toggle checked={prefs.data.moments_private} onChange={(v) => setPrefs.mutate(v)} label="New moments are private" />
            )}
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => setAdding(true)}>Person</Button>
          </>
        }
      />
      {isLoading ? (
        <Spinner />
      ) : sorted.length ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {sorted.map((p, i) => {
            const bits = [
              p.relation ?? "—",
              (p.hours_together ?? 0) > 0 ? `${num(p.hours_together, 1)} h` : null,
              (p.money_in ?? 0) > 0 ? `+${money(p.money_in ?? 0, currency)}` : null,
              (p.money_out ?? 0) > 0 ? `−${money(p.money_out ?? 0, currency)}` : null,
              p.gifts ? pluralize(p.gifts, "gift") : null,
              typeof p.moments === "number" && p.moments ? pluralize(p.moments, "moment") : null,
            ].filter(Boolean);
            return (
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
                  <p className="truncate text-[12px] text-ink-3">{bits.join(" · ")}</p>
                  {p.last_interaction && <p className="truncate text-[12px] text-ink-3">last {shortDate(p.last_interaction)}</p>}
                </div>
              </motion.button>
            );
          })}
        </div>
      ) : (
        <Card><Empty icon={<Users size={24} />} title="No one yet" /></Card>
      )}
      <PersonModal id={open} people={data ?? []} privateDefault={prefs.data?.moments_private ?? false} onClose={() => setOpen(null)} />
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
