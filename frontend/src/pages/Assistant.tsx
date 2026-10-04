import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, Bot, Cpu, Inbox, MessageSquarePlus, ScrollText, Search, Square, Trash2, Undo2, Wrench } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useSearchParams } from "react-router";
import remarkGfm from "remark-gfm";
import { CaptureReview, useInvalidateLedger } from "../components/domain";
import { Badge, Button, Card, Empty, ErrorNote, IconButton, Input, PageHeader, Spinner, Tabs, Textarea, Toggle, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { addDays, clock, longDate, pluralize, shortDate } from "../lib/format";
import { useTimeZone, useToday } from "../lib/hooks";
import { postStream } from "../lib/sse";
import type { AiStatus, CaptureDraft, ChatAction, ChatMessage, ChatThread, DayReview, JournalTime, ReviewFacts, SearchHit } from "../lib/types";

const SUGGESTIONS = [
  "How did I spend the last 7 days, compared with my targets?",
  "What did I write about death and time?",
  "When did I talk about AI with my family?",
  "I studied linear algebra from 06:10 to 08:30 and did the workout routine at 17:15 for 50 minutes.",
  "Plan tomorrow as a [Sprint] day around the internship.",
  "Which hours of the day am I most likely to fall into noise?",
  "What did I watch on YouTube yesterday, and for how long?",
];

const TOOL_LABEL: Record<string, string> = {
  search_memory: "searched the journal",
  get_time_summary: "read the ledger",
  get_day: "read the day",
  log_time: "logged time",
  start_timer: "started the timer",
  stop_timer: "stopped the timer",
  log_expense: "recorded money",
  log_habit: "logged a habit",
  add_to_journal: "wrote in the journal",
  list_goals: "read the goals",
  update_goal: "updated a goal",
  create_goal: "created a goal",
  get_life_numbers: "computed life numbers",
  get_youtube: "read the YouTube history",
  get_prayer_times: "read the prayer times",
  get_spending: "read the money",
  get_habit_progress: "read the habit streaks",
  list_life_events: "read the life events",
  add_life_event: "added a life event",
  plan_block: "planned a block",
};

interface LiveTurn {
  user: string;
  text: string;
  tools: { name: string; args?: Record<string, unknown>; result?: string }[];
  memory: number;
  error: string | null;
  done: boolean;
  actions: ChatAction[];
  citations: SearchHit[];
  messageId: number | null;
}

function Citations({ hits }: { hits: SearchHit[] }) {
  const unique = hits.filter((h, i) => hits.findIndex((x) => x.source_type === h.source_type && x.source_id === h.source_id) === i);
  if (!unique.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {unique.slice(0, 8).map((h) => (
        <Link key={`${h.source_type}${h.source_id}`} to={h.source_type === "journal" ? `/journal?entry=${h.source_id}` : `/journal?note=${h.source_id}`}>
          <Badge tone="accent">{h.day_number ? `Day ${h.day_number}` : h.title}</Badge>
        </Link>
      ))}
    </div>
  );
}

/** What the assistant did, each with its own undo. */
function Actions({ actions, messageId }: { actions: ChatAction[]; messageId: number | null }) {
  const [state, setState] = useState(actions);
  const invalidate = useInvalidateLedger();
  const qc = useQueryClient();
  const toast = useToast();
  useEffect(() => setState(actions), [actions]);
  const undo = useMutation({
    mutationFn: (i: number) => api.post<{ message: string; actions: ChatAction[] }>(`/api/ai/messages/${messageId}/actions/${i}/undo`),
    onSuccess: (r) => {
      setState(r.actions);
      invalidate();
      for (const key of ["journal", "goals", "chat", "life", "plan"]) qc.invalidateQueries({ queryKey: [key] });
      toast(r.message, "good");
    },
    onError: (e) => toast(errorMessage(e), "critical"),
  });
  if (!state.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {state.map((a, i) => (
        <span key={i} className={clsx("inline-flex items-center gap-1 rounded-lg py-0.5 pl-2 pr-1 text-[12px] font-medium",
          a.undone ? "bg-panel-hover text-ink-3 line-through" : "bg-good/15 text-good")}>
          ✓ {a.label}
          {messageId !== null && !a.undone && (
            <button
              onClick={() => undo.mutate(i)}
              disabled={undo.isPending}
              className="ml-1 inline-flex items-center gap-0.5 rounded-md px-1 text-ink-3 no-underline hover:bg-panel-hover hover:text-ink"
              title="Undo this"
            >
              <Undo2 size={11} /> undo
            </button>
          )}
        </span>
      ))}
    </div>
  );
}

function ToolChips({ tools }: { tools: { name: string; result?: string }[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!tools.length) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {tools.map((t, i) => (
        <button key={i} onClick={() => setOpen(open === i ? null : i)} className="inline-flex items-center gap-1 rounded-lg bg-panel-hover px-2 py-0.5 text-[12px] text-ink-3 hover:text-ink-2">
          <Wrench size={11} /> {TOOL_LABEL[t.name] ?? t.name}
        </button>
      ))}
      <AnimatePresence>
        {open !== null && tools[open]?.result && (
          <motion.pre
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="scroll-thin mt-1 max-h-48 w-full overflow-auto whitespace-pre-wrap rounded-lg bg-panel-hover p-2 text-[11px] text-ink-3"
          >
            {tools[open].result}
          </motion.pre>
        )}
      </AnimatePresence>
    </div>
  );
}

function Bubble({ role, children }: { role: "user" | "assistant"; children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className={clsx("flex gap-3", role === "user" && "justify-end")}
    >
      {role === "assistant" && (
        <span className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
          <Bot size={16} />
        </span>
      )}
      <div
        className={clsx(
          "max-w-[min(720px,85%)] rounded-2xl px-4 py-3 text-[14px]",
          role === "user" ? "bg-accent text-accent-ink" : "glass text-ink-2",
        )}
      >
        {children}
      </div>
    </motion.div>
  );
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-ol">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function StoredMessages({ messages }: { messages: ChatMessage[] }) {
  // Fold tool calls and tool results into the assistant answer that follows them.
  const turns: React.ReactNode[] = [];
  let pendingTools: { name: string; result?: string }[] = [];
  messages.forEach((m) => {
    if (m.role === "user") {
      turns.push(<Bubble key={m.id} role="user"><p className="whitespace-pre-wrap">{m.content}</p></Bubble>);
    } else if (m.role === "tool") {
      const t = [...pendingTools].reverse().find((x) => x.name === m.name && x.result === undefined);
      if (t) t.result = m.content;
    } else if (m.tool_calls?.length) {
      pendingTools.push(...m.tool_calls.map((c) => ({ name: c.name })));
    } else {
      const tools = pendingTools;
      pendingTools = [];
      turns.push(
        <Bubble key={m.id} role="assistant">
          <ToolChips tools={tools} />
          <Markdown text={m.content} />
          <Actions actions={m.meta?.actions ?? []} messageId={m.id} />
          <Citations hits={m.meta?.citations ?? []} />
        </Bubble>,
      );
    }
  });
  return <>{turns}</>;
}

function Chat({ threadId, setThreadId, status }: { threadId: number | null; setThreadId: (id: number | null) => void; status?: AiStatus }) {
  const qc = useQueryClient();
  const invalidate = useInvalidateLedger();
  const [input, setInput] = useState("");
  const [live, setLive] = useState<LiveTurn | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const messages = useQuery({
    queryKey: ["chat", threadId],
    queryFn: () => api.get<ChatMessage[]>(`/api/ai/threads/${threadId}/messages`),
    enabled: threadId !== null,
  });
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.data, live?.text, live?.tools.length]);

  async function send(text: string) {
    if (!text.trim() || (live && !live.done)) return;
    setInput("");
    const turn: LiveTurn = { user: text, text: "", tools: [], memory: 0, error: null, done: false, actions: [], citations: [], messageId: null };
    setLive(turn);
    abort.current = new AbortController();
    let newThread: number | null = threadId;
    try {
      await postStream("/api/ai/chat", { message: text, thread_id: threadId }, {
        signal: abort.current.signal,
        onEvent: (event, data) => {
          setLive((cur) => {
            if (!cur) return cur;
            const next = { ...cur };
            if (event === "start" && typeof data.thread_id === "number") newThread = data.thread_id;
            if (event === "token") next.text = cur.text + String(data.text ?? "");
            if (event === "memory") next.memory = Number(data.passages ?? 0);
            if (event === "tool_call") {
              next.tools = [...cur.tools, { name: String(data.name), args: data.args as Record<string, unknown> }];
              next.text = ""; // text before a tool call was thinking aloud; the answer follows
            }
            if (event === "tool_result") {
              const tools = [...cur.tools];
              const idx = tools.map((t) => t.name).lastIndexOf(String(data.name));
              if (idx >= 0) tools[idx] = { ...tools[idx], result: String(data.content ?? "") };
              next.tools = tools;
            }
            if (event === "error") next.error = String(data.message ?? "Unknown error");
            if (event === "done") {
              next.done = true;
              next.actions = (data.actions as ChatAction[]) ?? [];
              next.citations = (data.citations as SearchHit[]) ?? [];
              next.messageId = typeof data.message_id === "number" ? data.message_id : null;
              if (typeof data.answer === "string" && data.answer) next.text = data.answer;
            }
            return next;
          });
        },
      });
    } catch (e) {
      if ((e as Error).name !== "AbortError") setLive((cur) => (cur ? { ...cur, error: String(e), done: true } : cur));
    } finally {
      setLive((cur) => (cur ? { ...cur, done: true } : cur));
      if (newThread !== null && newThread !== threadId) setThreadId(newThread);
      await qc.invalidateQueries({ queryKey: ["chat"] });
      qc.invalidateQueries({ queryKey: ["threads"] });
      qc.invalidateQueries({ queryKey: ["ai-usage"] });
      for (const key of ["journal", "goals", "life", "plan"]) qc.invalidateQueries({ queryKey: [key] });
      invalidate();
      setLive((cur) => (cur && !cur.error ? null : cur));
    }
  }

  const empty = !messages.data?.length && !live;
  return (
    <div className="flex h-[calc(100dvh-14rem)] min-h-[520px] flex-col">
      <div className="scroll-thin flex-1 space-y-4 overflow-y-auto pr-1">
        {empty ? (
          <div className="flex h-full flex-col items-center justify-center gap-6 py-8 text-center">
            <div>
              <motion.div initial={{ scale: 0.9, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="mx-auto mb-3 grid h-14 w-14 place-items-center rounded-2xl bg-accent-soft text-accent">
                <Bot size={26} />
              </motion.div>
              <p className="text-lg font-semibold">Ask your record</p>
              <p className="mx-auto mt-1 max-w-md text-sm text-ink-3">It reads your ledger, journal, goals and habits — and can log what you tell it.</p>
            </div>
            <div className="grid w-full max-w-3xl gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} className="glass rounded-xl px-3 py-2.5 text-left text-[13px] text-ink-2 transition hover:bg-panel-hover hover:text-ink">
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.data && <StoredMessages messages={messages.data} />}
            {live && (
              <>
                <Bubble role="user"><p className="whitespace-pre-wrap">{live.user}</p></Bubble>
                <Bubble role="assistant">
                  {live.memory > 0 && <p className="mb-1 inline-flex items-center gap-1 text-[12px] text-ink-3"><Search size={11} /> {live.memory} passages from your journal</p>}
                  <ToolChips tools={live.tools} />
                  {live.text ? <Markdown text={live.text} /> : !live.error && <span className="text-ink-3">Thinking<span className="animate-caret">…</span></span>}
                  {!live.done && live.text && <span className="ml-0.5 inline-block h-4 w-1.5 animate-caret bg-accent align-middle" />}
                  {live.error && <div className="mt-2"><ErrorNote error={new Error(live.error)} /></div>}
                  <Actions actions={live.actions} messageId={live.messageId} />
                  <Citations hits={live.citations} />
                </Bubble>
              </>
            )}
          </>
        )}
        <div ref={bottom} />
      </div>
      <form
        className="mt-4"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <div className="glass flex items-end gap-2 rounded-2xl p-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            placeholder="Ask, or tell it what you did…  (Enter to send, Shift+Enter for a new line)"
            className="max-h-40 min-h-[44px] flex-1 resize-none border-0 bg-transparent focus:ring-0"
          />
          {live && !live.done ? (
            <IconButton label="Stop" onClick={() => abort.current?.abort()} className="h-11 w-11 bg-panel-hover">
              <Square size={16} />
            </IconButton>
          ) : (
            <Button type="submit" variant="primary" className="h-11 w-11 p-0" disabled={!input.trim()} aria-label="Send">
              <ArrowUp size={18} />
            </Button>
          )}
        </div>
        {status && (
          <p className="mt-2 flex items-center gap-1.5 text-[11px] text-ink-3">
            <Cpu size={11} /> {status.chain.length ? status.chain.join(" → ") : "No model configured — see Settings → AI"}
          </p>
        )}
      </form>
    </div>
  );
}

/** The journal reader: lines with a time become drafts here, one per day. */
function JournalTimeBar() {
  const qc = useQueryClient();
  const tz = useTimeZone();
  const state = useQuery({
    queryKey: ["journal-time"],
    queryFn: () => api.get<JournalTime>("/api/ai/journal-time"),
    refetchInterval: (q) => (q.state.data?.scan.state === "running" ? 2500 : 60_000),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["journal-time"] });
    qc.invalidateQueries({ queryKey: ["drafts"] });
  };
  const read = useMutation({ mutationFn: () => api.post("/api/ai/journal-time/scan", { all: false }), onSuccess: refresh });
  const prefs = useMutation({ mutationFn: (scan: boolean) => api.put("/api/ai/journal-time/prefs", { scan }), onSuccess: refresh });
  const d = state.data;
  const running = d?.scan.state === "running";
  const ran = useRef(false);
  useEffect(() => {
    if (running) ran.current = true;
    else if (ran.current) {
      ran.current = false;
      qc.invalidateQueries({ queryKey: ["drafts"] });
    }
  }, [running, qc]);
  if (!d) return null;
  const error = d.scan.state === "error" ? d.scan.error : d.last_error;
  const status = running
    ? "Reading the lines of your journal that hold a time…"
    : d.ai_off
      ? "AI is off (Settings → AI): the journal cannot be read for time blocks."
      : error
        ? `The last reading failed: ${error}`
        : d.last_scan
          ? `Journal read at ${clock(d.last_scan, tz)}. ${d.days_not_read ? `${d.days_not_read} page(s) changed since: read after 10 quiet minutes.` : "Up to date."}`
          : "Your journal has not been read for time blocks yet: it starts by itself within minutes, or now.";
  return (
    <Card solid>
      <div className="flex flex-wrap items-center gap-3">
        <ScrollText size={18} className="shrink-0 text-ink-3" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink">From your journal</p>
          <p className={clsx("text-[13px]", error && !running ? "text-warning" : "text-ink-3")}>{status}</p>
          <p className="text-[12px] text-ink-3">Only the lines that hold a time are read; each day's blocks wait here until you save them.</p>
        </div>
        <Toggle checked={d.prefs.scan} onChange={(v) => prefs.mutate(v)} label="By themselves" />
        <Button size="sm" variant="secondary" onClick={() => read.mutate()} loading={running || read.isPending} disabled={d.ai_off}>
          Read now
        </Button>
      </div>
      <ErrorNote error={read.error ?? prefs.error} />
    </Card>
  );
}

function InboxTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const invalidate = useInvalidateLedger();
  const drafts = useQuery({ queryKey: ["drafts"], queryFn: () => api.get<CaptureDraft[]>("/api/ai/drafts") });
  const saved = useQuery({ queryKey: ["drafts", "committed"], queryFn: () => api.get<CaptureDraft[]>("/api/ai/drafts?status=committed&limit=10") });
  const [open, setOpen] = useState<number | null>(null);
  const undo = useMutation({
    mutationFn: (id: number) => api.post<{ removed: Record<string, number>; notes: string[] }>(`/api/ai/drafts/${id}/undo`),
    onSuccess: (r, id) => {
      invalidate();
      for (const key of ["drafts", "journal"]) qc.invalidateQueries({ queryKey: [key] });
      const parts = Object.entries(r.removed).filter(([, n]) => n).map(([k, n]) => `${n} ${k.replace("_", " ")}`);
      toast(`Undone${parts.length ? `: ${parts.join(", ")} removed` : ""}. The draft is back for review.`, "good");
      r.notes.forEach((n) => toast(n));
      setOpen(id);
    },
    onError: (e) => toast(errorMessage(e), "critical"),
  });
  const current = drafts.data?.find((d) => d.id === open) ?? drafts.data?.[0];
  if (drafts.isLoading) return <Spinner />;
  return (
    <div className="space-y-5">
      <JournalTimeBar />
      {drafts.data?.length ? (
        <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
          <ul className="space-y-1.5">
            {drafts.data.map((d) => (
              <li key={d.id}>
                <button onClick={() => setOpen(d.id)} className={clsx("w-full rounded-xl border border-line px-3 py-2 text-left hover:bg-panel-hover", current?.id === d.id && "bg-panel-hover")}>
                  <p className="text-[13px] font-medium">
                    {d.draft.origin === "journal" && (
                      <span className="mr-1.5 text-accent">{d.draft.day_number ? `Journal · Day ${d.draft.day_number}` : "Journal"}</span>
                    )}
                    {shortDate(d.date)} · {pluralize(d.draft.time_entries?.length ?? 0, "entry", "entries")}
                  </p>
                  <p className="line-clamp-2 text-[12px] text-ink-3">{d.input_text}</p>
                </button>
              </li>
            ))}
          </ul>
          {current && (
            <Card solid>
              <CaptureReview draft={current} onDone={() => setOpen(null)} allowJournal={current.draft.origin !== "journal"} />
            </Card>
          )}
        </div>
      ) : (
        <Empty icon={<Inbox size={22} />} title="Nothing waiting">Drafts from Capture and from your journal wait here until you review them.</Empty>
      )}
      {saved.data && saved.data.length > 0 && (
        <Card title="Saved recently" subtitle="A saved capture can be undone: its records are removed and the draft comes back here">
          <ul className="divide-y divide-line">
            {saved.data.map((d) => (
              <li key={d.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium text-ink">{shortDate(d.date)}</p>
                  <p className="truncate text-[12px] text-ink-3">{d.input_text}</p>
                </div>
                <Button size="sm" variant="ghost" icon={<Undo2 size={14} />} onClick={() => undo.mutate(d.id)} loading={undo.isPending && undo.variables === d.id}>
                  Undo
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function ReviewTab({ initialDay }: { initialDay: string | null }) {
  const today = useToday();
  const qc = useQueryClient();
  const [day, setDay] = useState(initialDay ?? addDays(today, -1));
  const facts = useQuery({
    queryKey: ["review", day],
    queryFn: () => api.get<{ facts: ReviewFacts; facts_text: string; stored: DayReview | null }>(`/api/ai/review/${day}`),
  });
  const history = useQuery({ queryKey: ["reviews"], queryFn: () => api.get<DayReview[]>("/api/ai/reviews?limit=14") });
  const write = useMutation({
    mutationFn: () => api.post<{ review: string; model: string }>(`/api/ai/review/${day}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review", day] });
      qc.invalidateQueries({ queryKey: ["reviews"] });
    },
  });
  useEffect(() => write.reset(), [day]); // eslint-disable-line react-hooks/exhaustive-deps
  const stored = facts.data?.stored;
  const text = write.data?.review ?? stored?.text ?? null;
  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="The facts" subtitle="Computed from your data — no AI involved" action={<Input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="w-44" />}>
          {facts.data ? <pre className="whitespace-pre-wrap font-sans text-[14px] leading-relaxed text-ink-2">{facts.data.facts_text}</pre> : <Spinner />}
        </Card>
        <Card
          title="Review"
          subtitle={longDate(day)}
          action={
            <Button variant={text ? "ghost" : "primary"} icon={<ScrollText size={15} />} onClick={() => write.mutate()} loading={write.isPending}>
              {text ? "Write again" : "Write it"}
            </Button>
          }
        >
          {text ? (
            <>
              <Markdown text={text} />
              <p className="mt-3 text-[11px] text-ink-3">{write.data?.model ?? stored?.model}</p>
            </>
          ) : (
            <>
              <p className="text-sm text-ink-3">
                One short, honest review: the numbers, what worked, one adjustment for tomorrow. One model request.
                {stored?.error ? ` Written automatically this morning without prose: ${stored.error}` : " Each morning, yesterday's is written by itself."}
              </p>
              <div className="mt-3"><ErrorNote error={write.error} /></div>
            </>
          )}
        </Card>
      </div>
      {history.data && history.data.length > 0 && (
        <Card title="Past reviews">
          <div className="flex flex-wrap gap-1.5">
            {history.data.map((r) => (
              <button key={r.date} onClick={() => setDay(r.date)}>
                <Badge tone={r.date === day ? "accent" : r.text ? "neutral" : "warning"}>
                  {shortDate(r.date)} · core {r.facts.core_hours}h
                </Badge>
              </button>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

export function AssistantPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as "chat" | "inbox" | "review") || "chat";
  const threadId = params.get("thread") ? Number(params.get("thread")) : null;
  const threads = useQuery({ queryKey: ["threads"], queryFn: () => api.get<ChatThread[]>("/api/ai/threads") });
  const status = useQuery({ queryKey: ["ai-status"], queryFn: () => api.get<AiStatus>("/api/ai/status"), staleTime: 60_000 });
  const usage = useQuery({ queryKey: ["ai-usage"], queryFn: () => api.get<{ model_calls_estimate: number }>("/api/ai/usage") });
  const drafts = useQuery({ queryKey: ["drafts"], queryFn: () => api.get<CaptureDraft[]>("/api/ai/drafts") });
  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/ai/threads/${id}`),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["threads"] });
      if (id === threadId) setParams({ tab: "chat" });
    },
  });
  const setTab = (t: string) => setParams({ tab: t, ...(threadId ? { thread: String(threadId) } : {}) });
  const setThread = (id: number | null) => setParams(id ? { tab: "chat", thread: String(id) } : { tab: "chat" });

  return (
    <div>
      <PageHeader
        title="Assistant"
        subtitle={
          !status.data
            ? undefined
            : status.data.cloud_first
              ? `Free models today: about ${usage.data?.model_calls_estimate ?? 0} answers and captures so far (the free tier allows 50 requests a day).`
              : status.data.chain.length
                ? "Local model: private and offline, slower. Add an OpenRouter key in backend/.env for faster answers."
                : "No model available — see Settings → AI."
        }
        actions={
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "chat", label: "Chat" },
              { id: "inbox", label: <span>Inbox{drafts.data?.length ? ` · ${drafts.data.length}` : ""}</span> },
              { id: "review", label: "Daily review" },
            ]}
          />
        }
      />
      {tab === "chat" && (
        <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
          <Card className="hidden lg:block" solid>
            <Button className="mb-3 w-full" icon={<MessageSquarePlus size={15} />} onClick={() => setThread(null)}>
              New conversation
            </Button>
            <ul className="scroll-thin max-h-[60dvh] space-y-1 overflow-y-auto">
              {threads.data?.map((t) => (
                <li key={t.id} className="group flex items-center">
                  <button onClick={() => setThread(t.id)} className={clsx("min-w-0 flex-1 truncate rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-panel-hover", threadId === t.id ? "bg-panel-hover text-ink" : "text-ink-2")}>
                    {t.title}
                  </button>
                  <IconButton label="Delete conversation" className="opacity-0 group-hover:opacity-100" onClick={() => remove.mutate(t.id)}>
                    <Trash2 size={14} />
                  </IconButton>
                </li>
              ))}
            </ul>
          </Card>
          <Chat key={threadId ?? "new"} threadId={threadId} setThreadId={setThread} status={status.data} />
        </div>
      )}
      {tab === "inbox" && <InboxTab />}
      {tab === "review" && <ReviewTab initialDay={params.get("day")} />}
    </div>
  );
}
