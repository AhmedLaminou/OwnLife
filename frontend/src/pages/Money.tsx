import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BookOpen, Check, Pencil, Plus, RefreshCw, Trash2, UserRound, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Columns, HBars } from "../components/charts";
import { Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, StatTile, Tabs, Toggle, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import { addDays, clock, longDate, money, pluralize, shortDate } from "../lib/format";
import { useProfile, useTimeZone, useToday } from "../lib/hooks";
import type { JournalMoney, MoneySummary, Person, Transaction } from "../lib/types";

const CATEGORIES = ["food", "transport", "phone", "clothing", "education", "health", "gift", "family", "shopping", "income", "other"];

/** Amounts written in the journal, read by the model, waiting for a yes or a no. */
function JournalMoneyCard({ currency }: { currency: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const tz = useTimeZone();
  const state = useQuery({
    queryKey: ["money", "journal"],
    queryFn: () => api.get<JournalMoney>("/api/money/journal"),
    refetchInterval: (q) => (q.state.data?.scan.state === "running" ? 2500 : 60_000),
  });
  const [unticked, setUnticked] = useState<Set<number>>(new Set());
  const refresh = () => qc.invalidateQueries({ queryKey: ["money"] });
  const scan = useMutation({ mutationFn: (all: boolean) => api.post("/api/money/journal/scan", { all }), onSuccess: refresh });
  const accept = useMutation({
    mutationFn: (ids: number[]) => api.post<{ added: number }>("/api/money/journal/accept", { ids }),
    onSuccess: (r) => {
      refresh();
      setUnticked(new Set());
      toast(`${pluralize(r.added, "transaction")} added from your journal`, "good");
    },
  });
  const dismiss = useMutation({
    mutationFn: (ids: number[]) => api.post("/api/money/journal/dismiss", { ids }),
    onSuccess: () => {
      refresh();
      setUnticked(new Set());
    },
  });
  const prefs = useMutation({ mutationFn: (p: JournalMoney["prefs"]) => api.put("/api/money/journal/prefs", p), onSuccess: refresh });
  const d = state.data;
  const running = d?.scan.state === "running";
  const ran = useRef(false);
  useEffect(() => {
    if (running) ran.current = true;
    else if (ran.current) {
      ran.current = false;
      refresh(); // a scan that adds without asking changes the totals too
    }
  }, [running]);
  if (!d) return null;
  const items = d.suggestions;
  const ticked = items.filter((s) => !unticked.has(s.id)).map((s) => s.id);
  const error = d.scan.state === "error" ? d.scan.error : d.last_error;
  const status = running
    ? "Reading your journal…"
    : d.ai_off
      ? "AI is off (Settings → AI): the journal cannot be read for money."
      : error
        ? `The last reading failed: ${error}`
        : d.last_scan
          ? `Read at ${clock(d.last_scan, tz)}. ${d.days_not_read ? `${pluralize(d.days_not_read, "page")} changed since: read after 10 quiet minutes.` : "Up to date."}`
          : "Not read yet: it starts by itself within minutes, or now with Read.";
  return (
    <Card
      title="Found in your journal"
      subtitle="Only the sentences that mention money go to the model, never the whole page"
      action={
        <Button size="sm" variant="secondary" icon={<RefreshCw size={13} />} onClick={() => scan.mutate(false)} loading={running || scan.isPending} disabled={d.ai_off}>
          Read
        </Button>
      }
    >
      <p className={clsx("text-[13px]", error && !running ? "text-warning" : "text-ink-3")}>{status}</p>
      {items.length > 0 && (
        <>
          <ul className="mt-3 divide-y divide-line">
            {items.map((s) => (
              <li key={s.id} className="flex items-start gap-3 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={!unticked.has(s.id)}
                  onChange={(e) => {
                    const next = new Set(unticked);
                    if (e.target.checked) next.delete(s.id);
                    else next.add(s.id);
                    setUnticked(next);
                  }}
                  className="mt-1 h-4 w-4 accent-[var(--accent)]"
                  aria-label={`Include ${s.item}`}
                />
                <span className="w-14 shrink-0 pt-0.5 text-[12px] text-ink-3">{shortDate(s.date)}</span>
                <span className="min-w-0 flex-1">
                  <span className="text-ink">{s.item}</span>
                  {s.counterparty && <span className="text-ink-3"> · {s.counterparty}</span>}
                  <span className="text-[12px] text-ink-3"> · {s.category}</span>
                  <span className="block truncate text-[12px] italic text-ink-3" title={s.quote}>“{s.quote}”</span>
                </span>
                <span className={clsx("num w-24 shrink-0 text-right font-medium", s.direction === "in" ? "text-good" : "text-ink")}>
                  {s.direction === "in" ? "+" : "−"}{money(s.amount, currency)}
                </span>
                <button
                  onClick={() => dismiss.mutate([s.id])}
                  className="mt-0.5 shrink-0 rounded-md p-0.5 text-ink-3 hover:bg-panel-hover hover:text-critical"
                  title="Not a payment of mine: dismiss it"
                  aria-label={`Dismiss ${s.item}`}
                >
                  <X size={14} />
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            <Button size="sm" variant="ghost" icon={<X size={13} />} disabled={!ticked.length} onClick={() => dismiss.mutate(ticked)} loading={dismiss.isPending}>
              Dismiss {ticked.length}
            </Button>
            <Button size="sm" variant="primary" icon={<Check size={13} />} disabled={!ticked.length} onClick={() => accept.mutate(ticked)} loading={accept.isPending}>
              Add {ticked.length}
            </Button>
          </div>
        </>
      )}
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 border-t border-line pt-3">
        <Toggle checked={d.prefs.journal_scan} onChange={(v) => prefs.mutate({ ...d.prefs, journal_scan: v })} label="Read new pages by themselves" />
        <Toggle checked={d.prefs.auto_add} onChange={(v) => prefs.mutate({ ...d.prefs, auto_add: v })} label="Add without asking" />
      </div>
      <ErrorNote error={scan.error ?? accept.error ?? dismiss.error ?? prefs.error} />
    </Card>
  );
}

type TxForm = { date: string; direction: "in" | "out"; amount: string; item: string; category: string; counterparty: string; person: string };

function dayTitle(day: string, today: string): string {
  if (day === today) return `Today · ${longDate(day).replace(/ \d{4}$/, "")}`;
  if (day === addDays(today, -1)) return `Yesterday · ${longDate(day).replace(/ \d{4}$/, "")}`;
  return longDate(day);
}

/** One transaction to add or correct. "Person": someone you know — linked, or added to People. */
function TxModal({ tx, open, onClose, currency, defaultDate }: { tx: Transaction | null; open: boolean; onClose: () => void; currency: string; defaultDate: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const people = useQuery({ queryKey: ["people"], queryFn: () => api.get<Person[]>("/api/people"), enabled: open });
  const blank: TxForm = { date: defaultDate, direction: "out", amount: "", item: "", category: "food", counterparty: "", person: "" };
  const [f, setF] = useState<TxForm>(blank);
  useEffect(() => {
    if (!open) return;
    setF(tx
      ? { date: tx.date, direction: tx.direction, amount: String(tx.amount), item: tx.item, category: tx.category,
          counterparty: tx.person ? "" : tx.counterparty ?? "", person: tx.person ?? "" }
      : blank);
  }, [open, tx]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => {
      const person = f.person.trim();
      const body = { date: f.date, direction: f.direction, amount: Number(f.amount), item: f.item, category: f.category,
                     counterparty: f.counterparty.trim() || person || null };
      // On a correction, an empty person unlinks; on an addition it is simply left out.
      return tx ? api.patch<Transaction>(`/api/money/${tx.id}`, { ...body, person })
                : api.post<Transaction>("/api/money", { ...body, person: person || null });
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["money"] });
      qc.invalidateQueries({ queryKey: ["people"] });
      (r.warnings ?? []).forEach((w) => toast(w));
      onClose();
    },
  });
  const known = new Set((people.data ?? []).map((p) => p.name.toLowerCase()));
  const isNew = f.person.trim() !== "" && people.data !== undefined && !known.has(f.person.trim().toLowerCase());
  return (
    <Modal open={open} onClose={onClose} title={tx ? "Correct a transaction" : "Add a transaction"}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Date"><Input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} required /></Field>
          <Field label="Direction">
            <Select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value as TxForm["direction"] })}>
              <option value="out">Spent</option>
              <option value="in">Received</option>
            </Select>
          </Field>
          <Field label={`Amount (${currency === "XOF" ? "FCFA" : currency})`}><Input type="number" min={1} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required /></Field>
        </div>
        <Field label="What"><Input value={f.item} onChange={(e) => setF({ ...f, item: e.target.value })} required placeholder="Taxi to the office" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Category">
            <Select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
              {[...new Set([...CATEGORIES, f.category])].map((c) => <option key={c}>{c}</option>)}
            </Select>
          </Field>
          <Field label="Shop, driver…" hint="Who was paid, when it is not someone you know">
            <Input value={f.counterparty} onChange={(e) => setF({ ...f, counterparty: e.target.value })} placeholder="the baker" />
          </Field>
        </div>
        <Field label={f.direction === "in" ? "Received from (a person you know)" : "Given to (a person you know)"}
          hint={isNew ? `“${f.person.trim()}” will be added to People` : "Linked to their page in People"}>
          <Input list="tx-people" value={f.person} onChange={(e) => setF({ ...f, person: e.target.value })} placeholder="Uncle Kofi" />
          <datalist id="tx-people">{(people.data ?? []).map((p) => <option key={p.id} value={p.name} />)}</datalist>
        </Field>
        <ErrorNote error={save.error} />
        <div className="flex justify-end"><Button type="submit" variant="primary" loading={save.isPending}>{tx ? "Save" : "Add"}</Button></div>
      </form>
    </Modal>
  );
}

export function MoneyPage() {
  const today = useToday();
  const qc = useQueryClient();
  const { data: profile } = useProfile();
  const currency = profile?.currency ?? "XOF";
  const [range, setRange] = useState("30");
  const [day, setDay] = useState<string | null>(null);
  const start = addDays(today, -(Number(range) - 1));
  const summary = useQuery({ queryKey: ["money", "summary", range], queryFn: () => api.get<MoneySummary>(`/api/money/summary${qs({ start, end: today })}`) });
  const list = useQuery({ queryKey: ["money", "list", range], queryFn: () => api.get<Transaction[]>(`/api/money${qs({ start, end: today })}`) });
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [adding, setAdding] = useState(false);
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/money/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["money"] }) });
  const s = summary.data;
  const groups = useMemo(() => {
    const out: { date: string; rows: Transaction[]; out: number; in: number; twice: Set<number> }[] = [];
    for (const t of list.data ?? []) {
      if (day && t.date !== day) continue;
      let g = out.find((x) => x.date === t.date);
      if (!g) out.push((g = { date: t.date, rows: [], out: 0, in: 0, twice: new Set() }));
      g.rows.push(t);
      g[t.direction] += t.amount;
    }
    for (const g of out) {
      const seen = new Map<string, number[]>();
      for (const t of g.rows) seen.set(`${t.direction}|${t.amount}`, [...(seen.get(`${t.direction}|${t.amount}`) ?? []), t.id]);
      for (const ids of seen.values()) if (ids.length > 1) ids.forEach((id) => g.twice.add(id));
    }
    return out;
  }, [list.data, day]);
  const todayRows = (list.data ?? []).filter((t) => t.date === today);
  const spentToday = todayRows.filter((t) => t.direction === "out").reduce((a, t) => a + t.amount, 0);
  const receivedToday = todayRows.filter((t) => t.direction === "in").reduce((a, t) => a + t.amount, 0);
  const days = s?.by_day ?? [];
  return (
    <div className="space-y-5">
      <PageHeader
        title="Money"
        subtitle="Every franc in and out — taxis, beans, gifts."
        actions={
          <>
            <Tabs value={range} onChange={(v) => { setRange(v); setDay(null); }} tabs={[{ id: "7", label: "7 days" }, { id: "30", label: "30 days" }, { id: "90", label: "90 days" }]} />
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => setAdding(true)}>Add</Button>
          </>
        }
      />
      <JournalMoneyCard currency={currency} />
      {!s ? (
        <Spinner />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Today" value={money(spentToday, currency)} sub={receivedToday ? `spent · received ${money(receivedToday, currency)}` : "spent"} />
            <StatTile label="Spent" value={money(s.total_out, currency)} sub={`${money(s.avg_out_per_day, currency)} a day over ${range} days`} />
            <StatTile label="Received" value={money(s.total_in, currency)} sub={`over ${range} days`} />
            <StatTile label="Net" value={money(s.net, currency)} sub={`over ${range} days`} />
          </div>
          <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
            <Card title="Spent per day" subtitle="Click a day to see its transactions">
              {days.length ? (
                <Columns
                  title="Spent"
                  data={days.map((d) => ({ label: shortDate(d.date), value: d.out, tip: d.date }))}
                  format={(v) => (v >= 1000 ? `${Math.round(v / 100) / 10}k` : String(Math.round(v)))}
                  labelEvery={Math.ceil(days.length / 8)}
                  color="var(--k-growth)"
                  selected={day ? days.findIndex((d) => d.date === day) : null}
                  onSelect={(i) => setDay(days[i].date === day ? null : days[i].date)}
                />
              ) : (
                <Empty title="Nothing recorded yet" />
              )}
            </Card>
            <Card title="Where it goes">
              {s.out_by_category.length ? (
                <HBars rows={s.out_by_category.map((c) => ({ key: c.category, label: c.category, value: c.amount, color: "var(--k-growth)" }))} format={(v) => money(v, currency)} />
              ) : (
                <Empty title="Nothing spent in this range" />
              )}
            </Card>
          </div>
          <Card
            title={day ? dayTitle(day, today) : "Transactions"}
            subtitle={day ? undefined : "Day by day; the newest first"}
            action={day ? <Button size="sm" variant="ghost" onClick={() => setDay(null)}>Every day</Button> : undefined}
          >
            {groups.length ? (
              <div className="space-y-4">
                {groups.map((g) => (
                  <section key={g.date}>
                    {!day && (
                      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-1.5">
                        <button className="text-[13px] font-semibold text-ink hover:text-accent" onClick={() => setDay(g.date)}>{dayTitle(g.date, today)}</button>
                        <span className="num text-[12px] text-ink-3">
                          {g.out > 0 && <>spent <span className="text-ink">{money(g.out, currency)}</span></>}
                          {g.out > 0 && g.in > 0 && " · "}
                          {g.in > 0 && <>received <span className="text-good">{money(g.in, currency)}</span></>}
                        </span>
                      </div>
                    )}
                    <ul className="divide-y divide-line">
                      {g.rows.map((t) => (
                        <li key={t.id} className="group flex items-center gap-3 py-2.5 text-sm">
                          <span className="min-w-0 flex-1 truncate text-ink">
                            {t.source === "journal" && <BookOpen size={12} className="mr-1.5 inline text-ink-3" aria-label="From the journal" />}
                            <span title={t.note ?? undefined}>{t.item}</span>
                            {t.person ? (
                              <span className="text-ink-2"> · <UserRound size={12} className="mb-0.5 inline text-accent" aria-label="Person" /> {t.person}</span>
                            ) : t.counterparty ? <span className="text-ink-3"> · {t.counterparty}</span> : null}
                            {g.twice.has(t.id) && (
                              <span className="ml-2 rounded-md bg-warning/15 px-1.5 py-0.5 text-[11px] text-warning" title="The same amount twice this day: was it recorded twice?">same amount twice?</span>
                            )}
                          </span>
                          <span className="hidden text-[12px] text-ink-3 sm:inline">{t.category}</span>
                          <span className={clsx("num w-28 text-right font-medium", t.direction === "in" ? "text-good" : "text-ink")}>
                            {t.direction === "in" ? "+" : "−"}{money(t.amount, t.currency)}
                          </span>
                          <span className="flex gap-1 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
                            <button onClick={() => setEditing(t)} className="text-ink-3 hover:text-accent" aria-label={`Correct ${t.item}`}><Pencil size={14} /></button>
                            <button onClick={() => confirm(`Delete “${t.item}”?`) && remove.mutate(t.id)} className="text-ink-3 hover:text-critical" aria-label={`Delete ${t.item}`}><Trash2 size={14} /></button>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            ) : (
              <Empty title={day ? "Nothing that day" : "No transactions in this range"}>Write them in your journal (they appear above to confirm), add them here, or tell the assistant.</Empty>
            )}
          </Card>
        </>
      )}
      <TxModal tx={editing} open={adding || editing !== null} onClose={() => { setAdding(false); setEditing(null); }} currency={currency} defaultDate={day ?? today} />
    </div>
  );
}
