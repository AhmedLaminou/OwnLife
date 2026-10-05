import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BookMarked, Check, ExternalLink, Lightbulb, RefreshCw, Undo2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Badge, Button, Card, Empty, ErrorNote, Input, Select, Spinner, Tabs, Textarea, Toggle, useToast } from "../components/ui";
import { api } from "../lib/api";
import { clock, pluralize, shortDate } from "../lib/format";
import { useTimeZone } from "../lib/hooks";
import type { EssayPassages, Idea, IdeasState } from "../lib/types";

type Status = Idea["status"];
const NEW = "__new__";

function IdeaCard({ idea, essays }: { idea: Idea; essays: IdeasState["essays"] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState(idea.quote);
  const [target, setTarget] = useState<string>(idea.essay ? String(idea.essay.id) : idea.new_essay ? NEW : "");
  const [newTitle, setNewTitle] = useState(idea.new_essay ?? "");
  useEffect(() => {
    setText(idea.quote);
    setTarget(idea.essay ? String(idea.essay.id) : idea.new_essay ? NEW : "");
    setNewTitle(idea.new_essay ?? "");
  }, [idea.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ideas"] });
    qc.invalidateQueries({ queryKey: ["notes"] });
  };
  const place = useMutation({
    mutationFn: () => api.post<{ note: { title: string } }>(`/api/ideas/${idea.id}/place`,
      target === NEW ? { new_title: newTitle, text } : { note_id: Number(target), text }),
    onSuccess: (r) => {
      refresh();
      toast(`Added to “${r.note.title}” — and its file`, "good");
    },
  });
  const unplace = useMutation({
    mutationFn: () => api.post<{ message: string }>(`/api/ideas/${idea.id}/unplace`),
    onSuccess: (r) => {
      refresh();
      toast(r.message, "good");
    },
  });
  const status = useMutation({ mutationFn: (s: Status) => api.post(`/api/ideas/${idea.id}/status`, { status: s }), onSuccess: refresh });
  const destination = target === NEW ? (newTitle.trim() ? `“${newTitle.trim()}”` : "a new essay") : essays.find((e) => String(e.id) === target)?.title;
  return (
    <Card solid>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-[15px] font-semibold text-ink">{idea.title}</h3>
          <p className="mt-0.5 text-[12px] text-ink-3">
            {idea.journal_entry_id ? (
              <Link to={`/journal?entry=${idea.journal_entry_id}`} className="hover:text-accent">Day {idea.day_number ?? "?"} · {shortDate(idea.date)}</Link>
            ) : `${shortDate(idea.date)}`}
          </p>
        </div>
        <Badge>{idea.domain}</Badge>
      </div>
      <p className="mt-2 text-sm text-ink-2">{idea.statement}</p>
      {idea.references.length > 0 && (
        <ul className="mt-2 space-y-1">
          {idea.references.map((r) => (
            <li key={`${r.author}${r.work}`} className="flex items-start gap-2 text-[13px]">
              <BookMarked size={13} className={clsx("mt-0.5 shrink-0", r.verified ? "text-accent" : "text-ink-3")} />
              <span className="min-w-0">
                <span className="text-ink">{r.author}, <i>{r.work}</i></span>
                <span className="text-ink-3"> — {r.why}</span>
                {r.verified && r.url ? (
                  <a href={r.url} target="_blank" rel="noreferrer" className="ml-1.5 inline-flex items-center gap-0.5 text-[12px] text-accent hover:underline">
                    Open Library <ExternalLink size={11} />
                  </a>
                ) : (
                  <span className="ml-1.5 text-[12px] text-warning" title="Not found in Open Library: check it before trusting it">
                    {r.verified === false ? "not found" : "unchecked"}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      {idea.status === "placed" ? (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-3 text-[13px]">
          <span className="text-ink-2">In <span className="font-medium text-ink">{idea.placed_in?.title ?? "an essay"}</span>{idea.placed_at && ` since ${shortDate(idea.placed_at.slice(0, 10))}`}</span>
          <Button size="sm" variant="ghost" icon={<Undo2 size={13} />} onClick={() => unplace.mutate()} loading={unplace.isPending}>Take it out</Button>
        </div>
      ) : (
        <div className="mt-3 space-y-2 border-t border-line pt-3">
          <label className="block text-[12px] text-ink-3">What goes into the essay — your words from the page; edit freely</label>
          <Textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} className="text-[14px]" />
          <div className="flex flex-wrap items-center gap-2">
            <Select value={target} onChange={(e) => setTarget(e.target.value)} className="!w-auto min-w-[200px]" aria-label="Essay">
              <option value="">Choose an essay…</option>
              {essays.map((e) => <option key={e.id} value={e.id}>{e.title}{e.words ? "" : " (empty)"}</option>)}
              <option value={NEW}>A new essay…</option>
            </Select>
            {target === NEW && (
              <Input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="On Memory" className="!w-48" aria-label="New essay title" />
            )}
            <Button size="sm" variant="primary" icon={<Check size={13} />} disabled={!target || (target === NEW && !newTitle.trim()) || !text.trim()}
              onClick={() => place.mutate()} loading={place.isPending}>
              Add to {destination ?? "the essay"}
            </Button>
            <span className="flex-1" />
            {idea.status === "new" ? (
              <>
                <Button size="sm" variant="ghost" onClick={() => status.mutate("kept")}>Keep</Button>
                <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={() => status.mutate("dismissed")}>Dismiss</Button>
              </>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => status.mutate("new")}>Back to new</Button>
            )}
          </div>
        </div>
      )}
      <ErrorNote error={place.error ?? unplace.error ?? status.error} />
    </Card>
  );
}

function EssayPassagesCard() {
  const [open, setOpen] = useState(false);
  const { data, isFetching, error } = useQuery({
    queryKey: ["ideas", "essays"],
    queryFn: () => api.get<EssayPassages[]>("/api/ideas/essays"),
    enabled: open,
    staleTime: 5 * 60_000,
  });
  return (
    <Card
      title="Passages that may belong to each essay"
      subtitle="Found by meaning with the local search index — no model, nothing leaves this computer"
      action={<Button size="sm" variant="secondary" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"}</Button>}
    >
      {open && (isFetching && !data ? <Spinner /> : (
        <div className="space-y-4">
          <ErrorNote error={error} />
          {(data ?? []).map((e) => (
            <section key={e.id}>
              <p className="text-[13px] font-semibold text-ink">{e.title} <span className="font-normal text-ink-3">· {e.words ? pluralize(e.words, "word") : "empty"}</span></p>
              {e.error ? <p className="text-[12px] text-warning">{e.error}</p> : e.passages.length ? (
                <ul className="mt-1 space-y-1.5">
                  {e.passages.map((p) => (
                    <li key={p.journal_entry_id} className="text-[13px]">
                      <Link to={`/journal?entry=${p.journal_entry_id}`} className="text-accent hover:underline">Day {p.day_number ?? "?"}</Link>
                      <span className="text-ink-2"> — “{p.text}”</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="text-[12px] text-ink-3">Nothing close enough yet.</p>}
            </section>
          ))}
        </div>
      ))}
    </Card>
  );
}

export function IdeasTab() {
  const qc = useQueryClient();
  const tz = useTimeZone();
  const [show, setShow] = useState<Status>("new");
  const state = useQuery({
    queryKey: ["ideas"],
    queryFn: () => api.get<IdeasState>("/api/ideas"),
    refetchInterval: (q) => (q.state.data?.scan.state === "running" ? 2500 : 60_000),
  });
  const scan = useMutation({ mutationFn: (all: boolean) => api.post("/api/ideas/scan", { all }), onSuccess: () => qc.invalidateQueries({ queryKey: ["ideas"] }) });
  const prefs = useMutation({ mutationFn: (scanOn: boolean) => api.put("/api/ideas/prefs", { scan: scanOn }), onSuccess: () => qc.invalidateQueries({ queryKey: ["ideas"] }) });
  const d = state.data;
  const running = d?.scan.state === "running";
  const ran = useRef(false);
  useEffect(() => {
    if (running) ran.current = true;
    else if (ran.current) {
      ran.current = false;
      qc.invalidateQueries({ queryKey: ["ideas"] });
    }
  }, [running, qc]);
  if (!d) return <Spinner />;
  const error = d.scan.state === "error" ? d.scan.error : d.last_error;
  const share = Math.round(d.thought_share * 100);
  const status = running
    ? "Reading your thought sections…"
    : !d.online
      ? "Reading ideas needs an online model (Settings → AI): the local one is too slow for whole sections."
      : error
        ? `The last reading failed: ${error}`
        : d.last_scan
          ? `Read at ${clock(d.last_scan, tz)}. ${d.pages_waiting ? `${pluralize(d.pages_waiting, "page")} changed since.` : "Up to date."}`
          : `Not read yet: ${pluralize(d.pages_waiting, "page")} with thought sections.`;
  const ideas = d.ideas.filter((i) => i.status === show);
  return (
    <div className="space-y-5">
      <Card
        title="Ideas from your thoughts"
        subtitle={`Your [SomeThoughts] sections and “Idea :” lines, read by ${d.models[0] ?? "an online model"}; your essays get the ones you choose`}
        action={
          <Button size="sm" variant="secondary" icon={<RefreshCw size={13} />} onClick={() => scan.mutate(false)} loading={running || scan.isPending} disabled={!d.online}>
            Read
          </Button>
        }
      >
        <p className={clsx("text-[13px]", error && !running ? "text-warning" : "text-ink-3")}>{status}</p>
        {share > 0 && (
          <p className="mt-2 rounded-xl border border-line bg-panel-hover/40 px-3 py-2 text-[12px] leading-relaxed text-ink-2">
            Your thought sections are <span className="font-semibold text-ink">{share}%</span> of your journal: reading them sends that much of it
            to the online model — your private aliases removed, pages marked private never. Only the author and title of a reference go to Open Library.
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <Toggle checked={d.prefs.scan} onChange={(v) => prefs.mutate(v)} label="Read new pages by themselves" />
          <Button size="sm" variant="ghost" onClick={() => scan.mutate(true)} disabled={!d.online || running}>Read every page again</Button>
        </div>
        <ErrorNote error={scan.error ?? prefs.error} />
      </Card>
      <Tabs value={show} onChange={setShow} tabs={(["new", "kept", "placed", "dismissed"] as Status[]).map((s) => ({
        id: s, label: `${s === "new" ? "To place" : s === "kept" ? "Kept" : s === "placed" ? "In essays" : "Dismissed"} · ${d.counts[s]}`,
      }))} />
      {ideas.length ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {ideas.map((i) => <IdeaCard key={i.id} idea={i} essays={d.essays} />)}
        </div>
      ) : (
        <Card>
          <Empty icon={<Lightbulb size={22} />} title={show === "new" ? "No idea waiting" : "Nothing here"}>
            {show === "new" ? "Press Read to find the ideas in your thought sections, or switch on reading by itself." : null}
          </Empty>
        </Card>
      )}
      <EssayPassagesCard />
    </div>
  );
}
