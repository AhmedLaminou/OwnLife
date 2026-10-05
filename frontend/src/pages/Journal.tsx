import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  FileText,
  FileUp,
  GitMerge,
  History,
  Lock,
  NotebookPen,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Undo2,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { CaptureModal } from "../components/domain";
import { Badge, Button, Card, Empty, ErrorNote, Input, Modal, PageHeader, Select, Spinner, Tabs, Textarea, Toggle, useToast } from "../components/ui";
import { IdeasTab } from "./Ideas";
import { ApiError, api } from "../lib/api";
import { longDate, shortDate } from "../lib/format";
import { useDebounced, useToday } from "../lib/hooks";
import type { ImportReport, JournalEntry, Note, SearchHit, SearchStatus, SyncItem, SyncReport, SyncResult, SyncStatus } from "../lib/types";

const STOPWORDS = new Set("the and for with that this what when why how who did does have has had not was were are you your from about into than then there their les des une que qui est pour pas sur avec dans".split(" "));

function highlight(text: string, q: string) {
  const words = q.toLowerCase().split(/\W+/).filter((w) => w.length > 2 && !STOPWORDS.has(w));
  if (!words.length) return text;
  const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return text.split(re).map((part, i) =>
    words.includes(part.toLowerCase()) ? (
      <mark key={i} className="rounded bg-amber-soft px-0.5 text-ink">{part}</mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - Date.parse(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function reportText(r: SyncReport | ImportReport): string {
  const parts = [
    r.created && `${r.created} new`,
    r.updated && `${r.updated} updated from the file`,
    "pushed" in r && r.pushed && `${r.pushed} written to the file`,
    "notes_created" in r && r.notes_created && `${r.notes_created} new notes`,
    "notes_updated" in r && r.notes_updated && `${r.notes_updated} notes updated`,
    "restored" in r && r.restored && `${r.restored} restored`,
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "Everything already in sync";
}

/** Polls the live sync; when the watcher has applied a change, the pages refresh. */
export function useSyncStatus(poll = true) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["sync-status"],
    queryFn: () => api.get<SyncStatus>("/api/sync"),
    refetchInterval: poll ? 4000 : false,
  });
  const revision = useRef<number | null>(null);
  useEffect(() => {
    const r = status.data?.revision;
    if (r === undefined) return;
    if (revision.current !== null && r !== revision.current) {
      for (const key of ["journal", "notes", "search-status", "dashboard", "habits"]) qc.invalidateQueries({ queryKey: [key] });
    }
    revision.current = r;
  }, [status.data?.revision, qc]);
  return status;
}

function useSyncToast() {
  const toast = useToast();
  return (sync: SyncResult | null | undefined, fallback: string) => {
    if (sync?.state === "conflict") toast(sync.message ?? "A conflict with your file: choose a version", "critical");
    else if (sync?.state === "pending") toast(sync.message ?? "Saved in OwnLife; the file will be written later", "critical");
    else toast(sync?.message ?? fallback, "good");
  };
}

// ---------------------------------------------------------------- sync bar and panel
function SyncBar({ onOpenPanel }: { onOpenPanel: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const sync = useSyncStatus();
  const search = useQuery({
    queryKey: ["search-status"],
    queryFn: () => api.get<SearchStatus>("/api/search/status"),
    refetchInterval: (q) => (q.state.data?.indexing.state === "running" ? 2000 : false),
  });
  const refresh = () => {
    for (const key of ["journal", "notes", "search-status", "sync-status"]) qc.invalidateQueries({ queryKey: [key] });
  };
  const run = useMutation({
    mutationFn: () => api.post<SyncReport>("/api/sync/run"),
    onSuccess: (r) => {
      refresh();
      toast(reportText(r), "good");
      r.conflicts.forEach((c) => toast(c, "critical"));
    },
  });
  const upload = useMutation({
    mutationFn: (f: File) => api.upload<ImportReport>("/api/journal/import", f),
    onSuccess: (r) => {
      refresh();
      toast(reportText(r), "good");
    },
  });
  const reindex = useMutation({ mutationFn: () => api.post("/api/search/reindex"), onSuccess: () => qc.invalidateQueries({ queryKey: ["search-status"] }) });
  const s = sync.data;
  const st = search.data;
  const journalFiles = s?.files.filter((f) => f.kind === "journal") ?? [];
  const problems = (s?.conflicts.length ?? 0) + (s?.missing.length ?? 0) + (s?.pending.length ?? 0);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {s?.configured ? (
        <button
          onClick={onOpenPanel}
          className="glass inline-flex h-10 items-center gap-2.5 rounded-xl px-3 text-[13px] text-ink-2 transition hover:text-ink"
          title={journalFiles.map((f) => f.path).join("\n")}
        >
          <span className={clsx("relative flex h-2.5 w-2.5")}>
            {s.watching && !s.last_error && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-good opacity-40" />}
            <span className={clsx("relative inline-flex h-2.5 w-2.5 rounded-full", s.last_error ? "bg-critical" : s.watching ? "bg-good" : "bg-ink-3")} />
          </span>
          <span>
            {s.watching ? "Live sync" : "Sync"} with <span className="font-medium text-ink">{journalFiles.map((f) => f.name).join(", ") || "your journal file"}</span>
          </span>
          <span className="text-ink-3">· {s.last_change_at ? `last change ${ago(s.last_change_at)}` : `checked ${ago(s.last_sync_at)}`}</span>
          {problems > 0 && (
            <Badge tone="warning">
              <AlertTriangle size={11} /> {problems}
            </Badge>
          )}
        </button>
      ) : (
        s && <span className="text-[13px] text-ink-3">No journal file is synced (JOURNAL_SYNC_PATH in backend/.env).</span>
      )}
      {s?.configured && (
        <Button size="sm" variant="ghost" icon={<RefreshCw size={14} className={run.isPending ? "animate-spin" : ""} />} onClick={() => run.mutate()}>
          Sync now
        </Button>
      )}
      {s && !s.configured && (
        <>
          <Button variant="ghost" icon={<FileUp size={15} />} onClick={() => fileRef.current?.click()} loading={upload.isPending}>
            Import .md
          </Button>
          <input ref={fileRef} type="file" accept=".md,.txt" hidden onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
        </>
      )}
      {st && (
        <button onClick={() => reindex.mutate()} className="text-[12px] text-ink-3 hover:text-ink-2" title={st.embedder.message}>
          {st.indexing.state === "running"
            ? `Indexing ${st.indexing.done}/${st.indexing.total}…`
            : `${st.embedded}/${st.chunks} passages embedded${st.embedder.available ? "" : " · keyword search only"}`}
        </button>
      )}
      <ErrorNote error={run.error ?? upload.error} />
    </div>
  );
}

function SyncPanel({ open, onClose, onOpenItem }: { open: boolean; onClose: () => void; onOpenItem: (item: SyncItem) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: s } = useSyncStatus(open);
  const refresh = () => {
    for (const key of ["journal", "notes", "sync-status"]) qc.invalidateQueries({ queryKey: [key] });
  };
  const restore = useMutation({
    mutationFn: (id: number) => api.post<JournalEntry>(`/api/journal/${id}/restore`),
    onSuccess: (r) => {
      refresh();
      toast(r.sync?.message ?? "Put back into the file", "good");
    },
  });
  const drop = useMutation({
    mutationFn: (item: SyncItem) => api.del(item.type === "journal" ? `/api/journal/${item.id}` : `/api/notes/${item.id}`),
    onSuccess: () => {
      refresh();
      toast("Removed from OwnLife");
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="Sync with your files" wide>
      {!s ? (
        <Spinner />
      ) : (
        <div className="space-y-5 text-sm">
          <p className="leading-relaxed text-ink-2">
            Your Markdown files are the master copy. Saving in your editor reaches OwnLife within seconds; an edit here is written into
            the file at once — only the lines that changed, every other byte left as it was. Before each write, the previous version is
            copied to <code className="text-[12px]">{s.history_dir}</code>.
          </p>
          <div className="space-y-1.5">
            {s.files.map((f) => (
              <div key={f.path} className="flex items-center justify-between gap-3 rounded-xl border border-line px-3 py-2">
                <span className="flex min-w-0 items-center gap-2 text-ink-2">
                  <FileText size={15} className="shrink-0 text-ink-3" />
                  <span className="truncate" title={f.path}>{f.kind === "journal" ? f.name : `${f.name}/*.md`}</span>
                </span>
                <span className="shrink-0 text-[12px] text-ink-3">{f.kind === "journal" ? `${f.days} days` : `${f.notes} notes`}</span>
              </div>
            ))}
            {s.yearly && s.next_day_file && <p className="text-[12px] text-ink-3">A new page goes into its year's file: {s.next_day_file}</p>}
          </div>
          {s.last_error && <ErrorNote error={new Error(s.last_error)} />}

          {s.conflicts.length > 0 && (
            <section>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Changed in both places — choose</h3>
              {s.conflicts.map((c) => (
                <div key={`${c.type}${c.id}`} className="flex items-center justify-between gap-2 py-1">
                  <span className="text-ink">{c.label} {c.date && <span className="text-ink-3">· {shortDate(c.date)}</span>}</span>
                  <Button size="sm" icon={<GitMerge size={14} />} onClick={() => onOpenItem(c)}>Resolve</Button>
                </div>
              ))}
            </section>
          )}
          {s.missing.length > 0 && (
            <section>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">No longer in the file — hidden, not deleted</h3>
              {s.missing.map((m) => (
                <div key={`${m.type}${m.id}`} className="flex items-center justify-between gap-2 py-1">
                  <span className="text-ink">{m.label} {m.date && <span className="text-ink-3">· {shortDate(m.date)}</span>}</span>
                  <div className="flex gap-1">
                    {m.type === "journal" && (
                      <Button size="sm" icon={<Undo2 size={14} />} onClick={() => restore.mutate(m.id)} loading={restore.isPending}>Put back in the file</Button>
                    )}
                    <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => drop.mutate(m)}>Let it go</Button>
                  </div>
                </div>
              ))}
              <p className="mt-1 text-[12px] text-ink-3">A typo in a day's header hides that day; fix the header and it comes back by itself.</p>
            </section>
          )}
          {s.pending.length > 0 && (
            <section>
              <h3 className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Waiting to be written</h3>
              {s.pending.map((p) => <p key={`${p.type}${p.id}`} className="text-ink-2">{p.label}</p>)}
            </section>
          )}
          {!s.conflicts.length && !s.missing.length && !s.pending.length && (
            <p className="flex items-center gap-2 text-good"><History size={15} /> Everything is in sync.</p>
          )}
          <ErrorNote error={restore.error ?? drop.error} />
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- conflicts
function ConflictResolver({
  open,
  onClose,
  kind,
  item,
}: {
  open: boolean;
  onClose: () => void;
  kind: "journal" | "note";
  item: (JournalEntry | Note) & { body?: string; conflict_body?: string | null };
}) {
  const qc = useQueryClient();
  const syncToast = useSyncToast();
  const [merged, setMerged] = useState("");
  useEffect(() => {
    if (open) setMerged(item.conflict_body ?? item.body ?? "");
  }, [open, item]);
  const removedInFile = item.conflict_body === null || item.conflict_body === undefined;
  const resolve = useMutation({
    mutationFn: (keep: "file" | "app" | "text") =>
      api.post<{ sync?: SyncResult; deleted?: boolean }>(`/api/${kind === "journal" ? "journal" : "notes"}/${item.id}/resolve`, {
        keep,
        text: keep === "text" ? merged : null,
      }),
    onSuccess: (r) => {
      for (const key of ["journal", "notes", "sync-status"]) qc.invalidateQueries({ queryKey: [key] });
      syncToast(r.sync, r.deleted ? "Removed from OwnLife" : "Resolved");
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title="Two versions" wide>
      <div className="space-y-4">
        {removedInFile ? (
          <p className="text-sm text-ink-2">
            Your file no longer has this {kind === "journal" ? "day" : "note"}, but it was edited in OwnLife since. Put it back into the
            file, or let it go.
          </p>
        ) : (
          <>
            <p className="text-sm text-ink-2">It changed in your file and in OwnLife at the same time. Nothing was overwritten.</p>
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <p className="mb-1 text-[12px] font-medium uppercase tracking-wider text-ink-3">In your file</p>
                <Textarea readOnly rows={10} value={item.conflict_body ?? ""} className="text-[13px]" />
              </div>
              <div>
                <p className="mb-1 text-[12px] font-medium uppercase tracking-wider text-ink-3">In OwnLife</p>
                <Textarea readOnly rows={10} value={item.body ?? ""} className="text-[13px]" />
              </div>
            </div>
            <div>
              <p className="mb-1 text-[12px] font-medium uppercase tracking-wider text-ink-3">Or merge them by hand</p>
              <Textarea rows={8} value={merged} onChange={(e) => setMerged(e.target.value)} className="text-[14px]" />
            </div>
          </>
        )}
        <ErrorNote error={resolve.error} />
        <div className="flex flex-wrap justify-end gap-2">
          {removedInFile ? (
            <>
              <Button variant="ghost" onClick={() => resolve.mutate("file")} loading={resolve.isPending}>Let it go</Button>
              <Button variant="primary" onClick={() => resolve.mutate("app")} loading={resolve.isPending}>Put it back in the file</Button>
            </>
          ) : (
            <>
              <Button onClick={() => resolve.mutate("file")} loading={resolve.isPending}>Keep the file's version</Button>
              <Button onClick={() => resolve.mutate("app")} loading={resolve.isPending}>Keep OwnLife's version</Button>
              <Button variant="primary" icon={<GitMerge size={15} />} onClick={() => resolve.mutate("text")} loading={resolve.isPending}>Save the merge</Button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}

function SyncBadge({ state, file }: { state: string; file: string | null }) {
  if (state === "conflict") return <Badge tone="warning"><AlertTriangle size={11} /> two versions</Badge>;
  if (state === "pending") return <Badge tone="warning">waiting to be written</Badge>;
  if (file) {
    return (
      <span title={`Synced both ways with ${file}`}>
        <Badge><FileText size={11} /> {file}</Badge>
      </span>
    );
  }
  return <Badge>only in OwnLife</Badge>;
}

// ---------------------------------------------------------------- journal pages
function EntryEditor({ open, onClose, entry, defaultDate }: { open: boolean; onClose: () => void; entry: JournalEntry | null; defaultDate: string }) {
  const qc = useQueryClient();
  const syncToast = useSyncToast();
  const [form, setForm] = useState({ entry_date: defaultDate, body: "", is_private: false });
  useEffect(() => {
    if (open) setForm(entry ? { entry_date: entry.entry_date, body: entry.body ?? "", is_private: entry.is_private } : { entry_date: defaultDate, body: "", is_private: false });
  }, [open, entry, defaultDate]);
  const save = useMutation({
    mutationFn: () =>
      entry
        ? api.patch<JournalEntry>(`/api/journal/${entry.id}`, { ...form, expected_hash: entry.body_hash })
        : api.post<JournalEntry>("/api/journal", form),
    onSuccess: (r) => {
      for (const key of ["journal", "dashboard", "sync-status"]) qc.invalidateQueries({ queryKey: [key] });
      syncToast(r.sync, "Saved");
      onClose();
    },
  });
  const stale = save.error instanceof ApiError && save.error.status === 409;
  return (
    <Modal open={open} onClose={onClose} title={entry ? `Edit ${entry.day_number ? `Day ${entry.day_number}` : entry.entry_date}` : "New page"} wide>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Input type="date" value={form.entry_date} onChange={(e) => setForm({ ...form, entry_date: e.target.value })} className="w-48" />
          {entry?.file_name && <span className="text-[12px] text-ink-3">Saving writes the changed lines into {entry.file_name}.</span>}
        </div>
        <Textarea rows={18} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} className="text-[15px] leading-7" placeholder="[SomeFacts] [SomeThoughts]…" />
        <Toggle checked={form.is_private} onChange={(v) => setForm({ ...form, is_private: v })} label="Private — never sent to a cloud model" />
        <ErrorNote error={save.error} />
        <div className="flex justify-end gap-2">
          {stale && (
            <Button
              variant="ghost"
              onClick={() => {
                navigator.clipboard?.writeText(form.body).catch(() => undefined);
                qc.invalidateQueries({ queryKey: ["journal"] });
                onClose();
              }}
            >
              Copy my text and reopen
            </Button>
          )}
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending}>Save</Button>
        </div>
      </div>
    </Modal>
  );
}

function Reader({ id, onOpen }: { id: number; onOpen: (id: number) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [capture, setCapture] = useState(false);
  const [resolving, setResolving] = useState(false);
  const { data: e, isLoading } = useQuery({ queryKey: ["journal", "entry", id], queryFn: () => api.get<JournalEntry>(`/api/journal/${id}`) });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/journal/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["journal"] });
      toast(e?.file_name ? `Removed, from ${e.file_name} too (a copy is in the file history)` : "Removed");
    },
  });
  if (isLoading || !e) return <div className="grid h-64 place-items-center"><Spinner /></div>;
  return (
    <motion.article key={e.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[13px] text-ink-3">{longDate(e.entry_date)}</p>
          <h2 className="mt-0.5 text-2xl font-semibold tracking-tight">
            {e.day_number ? <>Day <span className="text-accent">{e.day_number}</span></> : e.title || "Journal"}
          </h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {e.tags.map((t) => <Badge key={t} tone={t.startsWith("{") ? "critical" : "accent"}>{t}</Badge>)}
            {e.is_private && <Badge><Lock size={11} /> private</Badge>}
            <Badge>{e.word_count} words</Badge>
            <SyncBadge state={e.sync_state} file={e.file_name} />
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Button size="sm" icon={<Sparkles size={14} />} onClick={() => setCapture(true)} title="Turn this day's text into ledger entries (AI, reviewed before saving)">
            Extract to ledger
          </Button>
          <Button size="sm" variant="ghost" icon={<Pencil size={14} />} onClick={() => setEditing(true)}>Edit</Button>
          <Button
            size="sm"
            variant="ghost"
            icon={<Trash2 size={14} />}
            onClick={() => confirm(e.file_name ? `Delete this page, also from ${e.file_name}? A copy of the file is kept in the file history.` : "Delete this page?") && remove.mutate()}
            aria-label="Delete"
          />
        </div>
      </header>
      {e.sync_state === "conflict" && (
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm">
          <span className="flex items-center gap-2 text-ink"><AlertTriangle size={16} className="text-warning" /> This day changed in your file and in OwnLife. Nothing was overwritten.</span>
          <Button size="sm" variant="primary" icon={<GitMerge size={14} />} onClick={() => setResolving(true)}>Choose a version</Button>
        </div>
      )}
      <ErrorNote error={remove.error} />
      <div className="whitespace-pre-wrap text-[15px] leading-[1.8] text-ink-2">{e.body}</div>
      <footer className="mt-8 flex justify-between border-t border-line pt-4">
        <Button variant="ghost" icon={<ChevronLeft size={16} />} disabled={!e.prev_id} onClick={() => e.prev_id && onOpen(e.prev_id)}>Previous day</Button>
        <Button variant="ghost" disabled={!e.next_id} onClick={() => e.next_id && onOpen(e.next_id)}>
          Next day <ChevronRight size={16} />
        </Button>
      </footer>
      <EntryEditor open={editing} onClose={() => setEditing(false)} entry={e} defaultDate={e.entry_date} />
      <CaptureModal open={capture} onClose={() => setCapture(false)} initialText={e.body ?? ""} initialDate={e.entry_date} fromJournal />
      <ConflictResolver open={resolving} onClose={() => setResolving(false)} kind="journal" item={e} />
    </motion.article>
  );
}

// ---------------------------------------------------------------- notes
function NoteEditor({ open, onClose, note }: { open: boolean; onClose: () => void; note: Note | null }) {
  const qc = useQueryClient();
  const syncToast = useSyncToast();
  const [form, setForm] = useState({ title: "", body: "", is_private: false, kind: "essay" });
  useEffect(() => {
    if (open) setForm(note ? { title: note.title, body: note.body ?? "", is_private: note.is_private, kind: note.kind } : { title: "", body: "", is_private: false, kind: "essay" });
  }, [open, note]);
  const save = useMutation({
    mutationFn: () =>
      note ? api.patch<Note>(`/api/notes/${note.id}`, { ...form, expected_hash: note.body_hash }) : api.post<Note>("/api/notes", form),
    onSuccess: (r) => {
      for (const key of ["notes", "sync-status"]) qc.invalidateQueries({ queryKey: [key] });
      syncToast(r.sync, "Saved");
      onClose();
    },
  });
  return (
    <Modal open={open} onClose={onClose} title={note ? `Edit “${note.title}”` : "New note"} wide>
      <div className="space-y-4">
        <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Reading notes" />
        <p className="text-[12px] text-ink-3">
          {note?.file_name
            ? `Saving writes into ${note.file_name}.`
            : "A new note becomes a Markdown file beside your journal, named after the title (ReadingNotes.md)."}
        </p>
        <Textarea rows={16} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} className="text-[15px] leading-7" />
        <div className="flex flex-wrap items-center gap-4">
          <Toggle checked={form.is_private} onChange={(v) => setForm({ ...form, is_private: v })} label="Private — never sent to a cloud model" />
          <label className="flex items-center gap-2 text-sm text-ink-2">
            Kind
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })} className="!w-auto" aria-label="Kind">
              <option value="essay">Essay — receives ideas</option>
              <option value="note">Note — receives ideas</option>
              <option value="reference">Reference — never receives ideas</option>
            </Select>
          </label>
        </div>
        <ErrorNote error={save.error} />
        <div className="flex justify-end">
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={!form.title.trim()}>Save</Button>
        </div>
      </div>
    </Modal>
  );
}

function NotesTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const [params] = useSearchParams();
  const [open, setOpen] = useState<number | null>(params.get("note") ? Number(params.get("note")) : null);
  const [editing, setEditing] = useState<Note | null | "new">(null);
  const [resolving, setResolving] = useState(false);
  useEffect(() => {
    if (params.get("note")) setOpen(Number(params.get("note")));
  }, [params]);
  const notes = useQuery({ queryKey: ["notes"], queryFn: () => api.get<Note[]>("/api/notes") });
  const note = useQuery({ queryKey: ["notes", open], queryFn: () => api.get<Note>(`/api/notes/${open}`), enabled: open !== null });
  const remove = useMutation({
    mutationFn: (id: number) => api.del<{ ok: boolean; file_copy: string | null }>(`/api/notes/${id}`),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["notes"] });
      setOpen(null);
      toast(r.file_copy ? "Note and file removed — a copy is in the file history" : "Note removed");
    },
  });
  const n = note.data;
  return (
    <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
      <Card
        title="Notes & essays"
        subtitle="The other .md files beside your journal, both ways"
        action={<Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setEditing("new")}>New</Button>}
      >
        {notes.data?.length ? (
          <ul className="space-y-1">
            {notes.data.map((x) => (
              <li key={x.id}>
                <button onClick={() => setOpen(x.id)} className={clsx("w-full rounded-xl px-3 py-2 text-left transition hover:bg-panel-hover", open === x.id && "bg-panel-hover")}>
                  <p className="flex items-center gap-2 text-sm font-medium text-ink">
                    {x.title}
                    {x.sync_state === "conflict" && <AlertTriangle size={13} className="text-warning" />}
                  </p>
                  <p className="line-clamp-2 text-[12px] text-ink-3">{x.word_count ? x.excerpt : "Empty — waiting to be written"}</p>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <Empty icon={<NotebookPen size={22} />} title="No notes yet">The .md files beside your journal appear here; new notes become files there.</Empty>
        )}
      </Card>
      <Card>
        {n ? (
          <article>
            <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-2xl font-semibold tracking-tight">{n.title}</h2>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {n.is_private && <Badge><Lock size={11} /> private</Badge>}
                  <Badge>{n.word_count} words</Badge>
                  <SyncBadge state={n.sync_state} file={n.file_name} />
                </div>
              </div>
              <div className="flex gap-1.5">
                <Button size="sm" variant="ghost" icon={<Pencil size={14} />} onClick={() => setEditing(n)}>Edit</Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Trash2 size={14} />}
                  aria-label="Delete"
                  onClick={() => confirm(n.file_name ? `Delete this note and ${n.file_name}? A copy is kept in the file history.` : "Delete this note?") && remove.mutate(n.id)}
                />
              </div>
            </header>
            {n.sync_state === "conflict" && (
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm">
                <span className="text-ink">Changed in the file and in OwnLife — nothing was overwritten.</span>
                <Button size="sm" variant="primary" icon={<GitMerge size={14} />} onClick={() => setResolving(true)}>Choose a version</Button>
              </div>
            )}
            <ErrorNote error={remove.error} />
            {n.body ? (
              <div className="whitespace-pre-wrap text-[15px] leading-[1.8] text-ink-2">{n.body}</div>
            ) : (
              <Empty title="Nothing written yet">
                <Button size="sm" icon={<Pencil size={14} />} onClick={() => setEditing(n)}>Write it</Button>
              </Empty>
            )}
            <ConflictResolver open={resolving} onClose={() => setResolving(false)} kind="note" item={n} />
          </article>
        ) : (
          <Empty title="Pick a note" />
        )}
      </Card>
      <NoteEditor open={editing !== null} onClose={() => setEditing(null)} note={editing === "new" ? null : editing} />
    </div>
  );
}

// ---------------------------------------------------------------- page
export function JournalPage() {
  const today = useToday();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"days" | "notes" | "ideas">(params.get("note") ? "notes" : params.get("tab") === "ideas" ? "ideas" : "days");
  const [q, setQ] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [panel, setPanel] = useState(false);
  const debounced = useDebounced(q, 300);
  const selected = params.get("entry") ? Number(params.get("entry")) : null;
  const list = useQuery({
    queryKey: ["journal", "list", tag],
    queryFn: () => api.get<{ total: number; items: JournalEntry[] }>(`/api/journal?limit=1000${tag ? `&tag=${encodeURIComponent(tag)}` : ""}`),
  });
  const tags = useQuery({ queryKey: ["journal", "tags"], queryFn: () => api.get<{ tag: string; count: number }[]>("/api/journal/tags") });
  const search = useQuery({
    queryKey: ["search", "journal", debounced],
    queryFn: () => api.get<{ hits: SearchHit[]; mode: string }>(`/api/search?q=${encodeURIComponent(debounced)}&k=20`),
    enabled: debounced.trim().length >= 2,
  });
  const open = (id: number) => setParams({ entry: String(id) });
  const items = list.data?.items ?? [];
  useEffect(() => {
    if (!selected && items.length && tab === "days") setParams({ entry: String(items[0].id) }, { replace: true });
  }, [selected, items, tab, setParams]);

  const openItem = (item: SyncItem) => {
    setPanel(false);
    if (item.type === "journal") {
      setTab("days");
      setParams({ entry: String(item.id) });
    } else {
      setTab("notes");
      setParams({ note: String(item.id) });
    }
  };

  return (
    <div>
      <PageHeader
        title="Virtual Memory"
        subtitle={`${list.data?.total ?? 0} pages · “so that even at 50, heck even 100, I'd know what I've been doing”`}
        actions={
          <>
            <Tabs value={tab} onChange={setTab} tabs={[{ id: "days", label: "Days" }, { id: "notes", label: "Notes" }, { id: "ideas", label: "Ideas" }]} />
            <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>New page</Button>
          </>
        }
      />
      <div className="mb-5"><SyncBar onOpenPanel={() => setPanel(true)} /></div>
      {tab === "ideas" ? (
        <IdeasTab />
      ) : tab === "notes" ? (
        <NotesTab />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[360px_1fr]">
          <Card className="lg:sticky lg:top-24 lg:max-h-[calc(100dvh-8rem)] lg:overflow-hidden" solid>
            <div className="relative mb-3">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by words or meaning…" className="pl-9" />
            </div>
            {tags.data && tags.data.length > 0 && !q && (
              <div className="mb-3 flex flex-wrap gap-1.5">
                {tags.data.slice(0, 10).map((t) => (
                  <button key={t.tag} onClick={() => setTag(tag === t.tag ? null : t.tag)}>
                    <Badge tone={tag === t.tag ? "accent" : "neutral"}>{t.tag} · {t.count}</Badge>
                  </button>
                ))}
              </div>
            )}
            <div className="scroll-thin -mx-2 max-h-[60dvh] overflow-y-auto px-2 lg:max-h-[calc(100dvh-17rem)]">
              <AnimatePresence mode="popLayout">
                {debounced.trim().length >= 2 ? (
                  <motion.ul key="search" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="space-y-1.5">
                    <li className="px-1 pb-1 text-[12px] text-ink-3">
                      {search.isFetching ? "Searching…" : `${search.data?.hits.length ?? 0} passages · ${search.data?.mode ?? ""} search`}
                    </li>
                    {search.data?.hits.map((h) => (
                      <li key={h.chunk_id}>
                        <button
                          onClick={() => (h.source_type === "journal" ? open(h.source_id) : (setTab("notes"), setParams({ note: String(h.source_id) })))}
                          className="w-full rounded-xl border border-line px-3 py-2 text-left transition hover:bg-panel-hover"
                        >
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <span className="text-[12px] font-medium text-ink">{h.day_number ? `Day ${h.day_number}` : h.title}</span>
                            <span className="text-[11px] text-ink-3">{h.via.join(" + ")}</span>
                          </div>
                          <p className="line-clamp-3 text-[12px] leading-relaxed text-ink-3">{highlight(h.text, debounced)}</p>
                        </button>
                      </li>
                    ))}
                  </motion.ul>
                ) : (
                  <motion.ul key="list" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="space-y-1">
                    {items.map((e) => (
                      <li key={e.id}>
                        <button
                          onClick={() => open(e.id)}
                          className={clsx("w-full rounded-xl px-3 py-2 text-left transition hover:bg-panel-hover", selected === e.id && "bg-panel-hover shadow-[inset_3px_0_0_var(--accent)]")}
                        >
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
                              {e.day_number ? `Day ${e.day_number}` : e.title || "Page"}
                              {e.sync_state === "conflict" && <AlertTriangle size={12} className="text-warning" />}
                            </span>
                            <span className="text-[11px] text-ink-3">{shortDate(e.entry_date)}</span>
                          </div>
                          <p className="line-clamp-2 text-[12px] text-ink-3">{e.excerpt}</p>
                        </button>
                      </li>
                    ))}
                    {!items.length && !list.isLoading && <Empty title="No pages yet">Write in your journal file, or the first page here.</Empty>}
                  </motion.ul>
                )}
              </AnimatePresence>
            </div>
          </Card>
          <Card>{selected ? <Reader id={selected} onOpen={open} /> : <Empty title="Pick a day" />}</Card>
        </div>
      )}
      <EntryEditor open={creating} onClose={() => setCreating(false)} entry={null} defaultDate={today} />
      <SyncPanel open={panel} onClose={() => setPanel(false)} onOpenItem={openItem} />
    </div>
  );
}
