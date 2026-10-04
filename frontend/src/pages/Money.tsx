import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BookOpen, Check, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Columns, HBars } from "../components/charts";
import { Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, StatTile, Tabs, Toggle, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import { addDays, clock, money, pluralize, shortDate } from "../lib/format";
import { useProfile, useTimeZone, useToday } from "../lib/hooks";
import type { JournalMoney, MoneySummary, Transaction } from "../lib/types";

const CATEGORIES = ["transport", "food", "clothing", "phone", "education", "health", "gift", "family", "other"];

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

export function MoneyPage() {
  const today = useToday();
  const qc = useQueryClient();
  const { data: profile } = useProfile();
  const currency = profile?.currency ?? "XOF";
  const [range, setRange] = useState("30");
  const start = addDays(today, -(Number(range) - 1));
  const summary = useQuery({ queryKey: ["money", "summary", range], queryFn: () => api.get<MoneySummary>(`/api/money/summary${qs({ start, end: today })}`) });
  const list = useQuery({ queryKey: ["money", "list", range], queryFn: () => api.get<Transaction[]>(`/api/money${qs({ start, end: today })}`) });
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ date: today, direction: "out", amount: "", item: "", category: "food", counterparty: "" });
  const create = useMutation({
    mutationFn: () => api.post("/api/money", { ...f, amount: Number(f.amount), counterparty: f.counterparty || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["money"] });
      setAdding(false);
      setF({ ...f, amount: "", item: "", counterparty: "" });
    },
  });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/money/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["money"] }) });
  const s = summary.data;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Money"
        subtitle="Every franc in and out — taxis, beans, gifts."
        actions={
          <>
            <Tabs value={range} onChange={setRange} tabs={[{ id: "7", label: "7 days" }, { id: "30", label: "30 days" }, { id: "90", label: "90 days" }]} />
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
            <StatTile label="Spent" value={money(s.total_out, currency)} sub={`${money(s.avg_out_per_day, currency)} a day`} />
            <StatTile label="Received" value={money(s.total_in, currency)} />
            <StatTile label="Net" value={money(s.net, currency)} />
            <StatTile label="Transactions" value={String(list.data?.length ?? 0)} />
          </div>
          <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr]">
            <Card title="Spent per day">
              {s.by_day.length ? (
                <Columns
                  title="Spent"
                  data={s.by_day.map((d) => ({ label: shortDate(d.date), value: d.out, tip: d.date }))}
                  format={(v) => Math.round(v).toLocaleString("en")}
                  labelEvery={Math.ceil(s.by_day.length / 8)}
                  color="var(--k-growth)"
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
          <Card title="Transactions">
            {list.data?.length ? (
              <ul className="divide-y divide-line">
                {list.data.map((t) => (
                  <li key={t.id} className="group flex items-center gap-3 py-2.5 text-sm">
                    <span className="w-16 text-[12px] text-ink-3">{shortDate(t.date)}</span>
                    <span className="min-w-0 flex-1 truncate text-ink">
                      {t.source === "journal" && <BookOpen size={12} className="mr-1.5 inline text-ink-3" aria-label="From the journal" />}
                      <span title={t.note ?? undefined}>{t.item}</span>
                      {t.counterparty && <span className="text-ink-3"> · {t.counterparty}</span>}
                    </span>
                    <span className="text-[12px] text-ink-3">{t.category}</span>
                    <span className={clsx("num w-28 text-right font-medium", t.direction === "in" ? "text-good" : "text-ink")}>
                      {t.direction === "in" ? "+" : "−"}{money(t.amount, t.currency)}
                    </span>
                    <button onClick={() => remove.mutate(t.id)} className="text-ink-3 opacity-0 hover:text-critical group-hover:opacity-100" aria-label="Delete">
                      <Trash2 size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty title="No transactions in this range">Write them in your journal (they appear above to confirm), add them here, or with quick-log (“-400 taxi #transport”).</Empty>
            )}
          </Card>
        </>
      )}
      <Modal open={adding} onClose={() => setAdding(false)} title="Add a transaction">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Date"><Input type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
            <Field label="Direction">
              <Select value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}>
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
                {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
              </Select>
            </Field>
            <Field label="From / to"><Input value={f.counterparty} onChange={(e) => setF({ ...f, counterparty: e.target.value })} placeholder="Uncle Karim" /></Field>
          </div>
          <ErrorNote error={create.error} />
          <div className="flex justify-end"><Button type="submit" variant="primary" loading={create.isPending}>Add</Button></div>
        </form>
      </Modal>
    </div>
  );
}
