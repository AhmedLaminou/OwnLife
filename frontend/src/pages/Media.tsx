import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Ban, BookOpen, Check, Clapperboard, FileUp, MonitorPlay, Plus, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Columns, HBars } from "../components/charts";
import { CategorySelect, useInvalidateLedger } from "../components/domain";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, StatTile, Tabs, Toggle, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import { addDays, clock, compactNum, hm, num, pluralize, shortDate } from "../lib/format";
import { useTimeZone, useToday } from "../lib/hooks";
import { KIND_LABEL, chartKinds, kindColor } from "../lib/kinds";
import type { BlockingPrefs, BlockingState, HistoryImport, Kind, MeasuredToday as MeasuredTodayData, MediaItem, SortedVideo, YoutubeSorting, YoutubeStats, YoutubeSummary } from "../lib/types";

const RANGES = [
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
  { id: "365", label: "1 year" },
  { id: "3650", label: "All" },
];

function MeasuredToday() {
  const tz = useTimeZone();
  const { data } = useQuery({ queryKey: ["measured"], queryFn: () => api.get<MeasuredTodayData>("/api/ingest/youtube/recent"), refetchInterval: 60_000 });
  if (!data) return null;
  if (!data.last_seen) {
    return (
      <Card title="Measured live" subtitle="The OwnLife YouTube extension measures the minutes a video actually plays">
        <Empty icon={<MonitorPlay size={22} />} title="The extension is not connected yet">
          Settings → Integrations → YouTube extension: load it in Chrome or Edge and paste a key. From then on, every video's real
          minutes land in the ledger by themselves.
        </Empty>
      </Card>
    );
  }
  return (
    <Card title="Today, measured" subtitle={`By the YouTube extension · last heartbeat ${clock(data.last_seen, tz)}`}>
      <div className="grid gap-4 md:grid-cols-[200px_1fr]">
        <div>
          <p className="text-[40px] font-semibold leading-none tracking-tight">{hm(data.today.seconds)}</p>
          <p className="mt-1 text-[12px] text-ink-3">actually playing, pauses excluded</p>
        </div>
        <ul className="space-y-1 text-[13px]">
          {data.segments.slice(0, 8).map((x) => (
            <li key={`${x.start}${x.url}`} className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-ink-2">
                <span className="num text-ink-3">{clock(x.start, tz)}</span> {x.title}
                {x.channel && <span className="text-ink-3"> · {x.channel}</span>}
              </span>
              <span className="num shrink-0 text-ink">{hm(x.seconds)}</span>
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

const LIMITS = [30, 60, 90, 120, 150, 180, 240];

/** Noise videos blocked by the extension once today's noise reaches the limit (until midnight). */
function BlockingCard() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["youtube-blocking"], queryFn: () => api.get<BlockingState>("/api/media/youtube/blocking"), refetchInterval: 60_000 });
  const [channel, setChannel] = useState("");
  const save = useMutation({
    mutationFn: (prefs: BlockingPrefs) => api.put<BlockingState>("/api/media/youtube/blocking", prefs),
    onSuccess: (r) => qc.setQueryData(["youtube-blocking"], r),
  });
  if (!data) return null;
  const p = data.prefs;
  const st = data.status;
  const set = (patch: Partial<BlockingPrefs>) => save.mutate({ ...p, ...patch });
  const mode = (id: number) => (p.always.includes(id) ? "always" : p.never.includes(id) ? "never" : "limit");
  const setMode = (id: number, m: string) =>
    set({ always: [...p.always.filter((x) => x !== id), ...(m === "always" ? [id] : [])], never: [...p.never.filter((x) => x !== id), ...(m === "never" ? [id] : [])] });
  const addChannel = (name: string) => {
    const n = name.trim();
    if (n && !p.channels.some((c) => c.toLowerCase() === n.toLowerCase())) set({ channels: [...p.channels, n] });
    setChannel("");
  };
  return (
    <Card
      title="Blocking noise"
      subtitle="The extension blocks the videos of your noise categories once today's noise reaches your limit — until midnight. Learning videos, and videos not sorted yet, always play."
      action={<Ban size={18} className={p.enabled ? "text-critical" : "text-ink-3"} />}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <Toggle checked={p.enabled} onChange={(v) => set({ enabled: v })} label="Block noise videos once today's noise reaches" />
          <Select value={p.limit_minutes} onChange={(e) => set({ limit_minutes: Number(e.target.value) })} className="!w-auto" aria-label="Daily noise limit">
            {[...new Set([...LIMITS, p.limit_minutes])].sort((a, b) => a - b).map((m) => <option key={m} value={m}>{hm(m * 60)}</option>)}
          </Select>
        </div>
        {p.enabled && (
          <p className={clsx("text-[13px]", st.active ? "font-medium text-critical" : "text-ink-2")}>
            {st.active
              ? `Blocking now: ${hm(st.noise_seconds)} of noise today. Noise videos play again at midnight.`
              : `Noise today: ${hm(st.noise_seconds)} of ${hm(st.limit_seconds)} — ${hm(Math.max(0, st.limit_seconds - st.noise_seconds))} left before the block.`}
          </p>
        )}
        <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
          {data.categories.map((c) => (
            <label key={c.id} className="flex items-center justify-between gap-3 text-[13px]">
              <span className="text-ink">{c.name}</span>
              <Select value={mode(c.id)} onChange={(e) => setMode(c.id, e.target.value)} className="!w-auto !h-8 text-[13px]" aria-label={`${c.name}: when blocked`}>
                <option value="limit">blocked after the limit</option>
                <option value="always">always blocked</option>
                <option value="never">never blocked</option>
              </Select>
            </label>
          ))}
        </div>
        <div>
          <p className="mb-1.5 text-[13px] font-medium text-ink">Channels blocked every day</p>
          <div className="flex flex-wrap items-center gap-1.5">
            {p.channels.map((c) => (
              <span key={c} className="inline-flex items-center gap-1 rounded-lg border border-line px-2 py-1 text-[12px] text-ink">
                {c}
                <button onClick={() => set({ channels: p.channels.filter((x) => x !== c) })} className="text-ink-3 hover:text-critical" aria-label={`Unblock ${c}`}>
                  <X size={12} />
                </button>
              </span>
            ))}
            <form className="flex gap-1.5" onSubmit={(e) => { e.preventDefault(); addChannel(channel); }}>
              <Input list="noise-channels" value={channel} onChange={(e) => setChannel(e.target.value)} placeholder="A channel's name" className="!h-8 !w-56 text-[13px]" aria-label="Channel to block" />
              <datalist id="noise-channels">{data.suggestions.filter((c) => !p.channels.includes(c)).map((c) => <option key={c} value={c} />)}</datalist>
              <Button size="sm" variant="secondary" type="submit" disabled={!channel.trim()}>Block</Button>
            </form>
          </div>
        </div>
        <p className="text-[12px] text-ink-3">
          Needs the extension 0.3.0: chrome://extensions → OwnLife → reload. When OwnLife is not running, nothing is blocked. The day runs from midnight to midnight.
        </p>
        <ErrorNote error={save.error} />
      </div>
    </Card>
  );
}

function VideoLine({ v }: { v: SortedVideo }) {
  return (
    <div className="min-w-0 flex-1">
      <a href={v.url} target="_blank" rel="noreferrer" className="block truncate text-[13px] text-ink hover:text-accent">{v.title}</a>
      <p className="truncate text-[12px] text-ink-3">{v.channel ?? "Unknown channel"} · {hm(v.seconds)}</p>
    </div>
  );
}

function SortingCard() {
  const qc = useQueryClient();
  const toast = useToast();
  const invalidate = useInvalidateLedger();
  const [whole, setWhole] = useState<Record<string, boolean>>({});
  const { data } = useQuery({
    queryKey: ["youtube-sorting"],
    queryFn: () => api.get<YoutubeSorting>("/api/media/youtube/sorting"),
    refetchInterval: (q) => (q.state.data?.status.state === "running" ? 2000 : 60_000),
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["youtube-sorting"] });
    qc.invalidateQueries({ queryKey: ["youtube"] });
    qc.invalidateQueries({ queryKey: ["measured"] });
    invalidate();
  };
  const prefs = useMutation({
    mutationFn: (body: Partial<YoutubeSorting["prefs"]>) => api.put<{ refiled: number }>("/api/media/youtube/sorting/prefs", body),
    onSuccess: refresh,
  });
  const run = useMutation({ mutationFn: (history: boolean) => api.post("/api/media/youtube/sorting/run", { history }), onSuccess: refresh });
  const choose = useMutation({
    mutationFn: ({ v, category_id }: { v: SortedVideo; category_id: number }) =>
      api.post(`/api/media/youtube/videos/${v.video_id}/category`, { category_id, whole_channel: !!whole[v.video_id] }),
    onSuccess: (_, { v }) => {
      refresh();
      toast(whole[v.video_id] ? `Every video of ${v.channel} is sorted (a rule)` : "Sorted", "good");
    },
  });
  // A run that was going on when the page opened: refresh the ledger once it ends.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (data?.status.state === "running") wasRunning.current = true;
    else if (wasRunning.current) {
      wasRunning.current = false;
      refresh();
    }
  });
  if (!data) return null;
  const st = data.status;
  return (
    <Card
      title="Sorting what you watch"
      subtitle={`Videos no rule places. Your local model (${data.model}) sorts them from their title and channel, on this laptop; what it cannot tell waits for you here.`}
      action={
        <Button variant="ghost" icon={<Sparkles size={15} />} onClick={() => run.mutate(false)} loading={run.isPending || st.state === "running"}
          disabled={!data.ai || !data.prefs.sort}>
          Sort now
        </Button>
      }
    >
      <div className="space-y-2">
        <div className="flex flex-col items-start gap-2">
          <Toggle checked={data.prefs.strict} onChange={(v) => prefs.mutate({ strict: v })}
            label={`Strict: until it is sorted, a video counts as noise (“${data.unsorted_category}”)`} />
          <Toggle checked={data.prefs.sort} onChange={(v) => prefs.mutate({ sort: v })} label="Let the local model sort new videos" />
        </div>
        {!data.ai && <p className="text-[13px] text-warning">AI is off (Settings → AI): nothing sorts the videos but you.</p>}
        {st.state === "running" && (
          <p className="flex items-center gap-2 text-[13px] text-ink-2">
            <Spinner /> {st.history ? `Sorting the history: ${st.done ?? 0} of ${st.total || "…"} videos` : "Sorting…"}
          </p>
        )}
        {data.history_unsorted > 0 && st.state !== "running" && data.ai && (
          <p className="flex flex-wrap items-center gap-2 text-[13px] text-ink-2">
            {pluralize(data.history_unsorted, "past video")} (Takeout, Chrome) still without a category.
            <Button size="sm" variant="secondary" onClick={() => run.mutate(true)} loading={run.isPending}>Sort the history too</Button>
            <span className="text-[12px] text-ink-3">titles only, on this laptop; a few minutes per hundred</span>
          </p>
        )}
        {st.state === "error" && st.error && <p className="text-[13px] text-warning">The last sorting stopped: {st.error}</p>}
        {st.state !== "running" && st.asked ? (
          <p className="text-[12px] text-ink-3">
            Last sorting: {st.sorted} sorted, {st.guesses} {st.guesses === 1 ? "guess" : "guesses"} of {st.asked} {st.asked === 1 ? "video" : "videos"}.
          </p>
        ) : null}
        <ErrorNote error={prefs.error ?? run.error ?? choose.error} />
      </div>
      <div className="mt-4">
        <p className="mb-1 text-[13px] font-medium text-ink">
          To sort <span className="text-ink-3">· {data.to_sort.length ? `${data.to_sort.length}, last 14 days` : "nothing waiting"}</span>
        </p>
        {data.to_sort.length > 0 && (
          <ul className="divide-y divide-line">
            {data.to_sort.map((v) => (
              <li key={v.video_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2">
                <VideoLine v={v} />
                {v.category && (
                  <Button size="sm" variant="ghost" icon={<Check size={14} />} onClick={() => choose.mutate({ v, category_id: v.category!.id })}>
                    {v.category.name}?
                  </Button>
                )}
                <CategorySelect value={null} noneLabel={v.category ? "Something else…" : "Choose…"} className="h-8 w-48"
                  onChange={(id) => id && choose.mutate({ v, category_id: id })} />
                {v.channel && (
                  <label className="flex items-center gap-1.5 text-[12px] text-ink-3">
                    <input type="checkbox" checked={!!whole[v.video_id]} onChange={(e) => setWhole({ ...whole, [v.video_id]: e.target.checked })} />
                    whole channel
                  </label>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {data.by_model.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[13px] text-ink-2">Sorted by the model · {data.by_model.length} — correct any</summary>
          <ul className="mt-1 divide-y divide-line">
            {data.by_model.map((v) => (
              <li key={v.video_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2">
                <VideoLine v={v} />
                <CategorySelect value={v.category?.id ?? null} allowNone={false} className="h-8 w-48"
                  onChange={(id) => id && id !== v.category?.id && choose.mutate({ v, category_id: id })} />
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

function YouTubeTab() {
  const today = useToday();
  const qc = useQueryClient();
  const toast = useToast();
  const invalidate = useInvalidateLedger();
  const fileRef = useRef<HTMLInputElement>(null);
  const [range, setRange] = useState("365");
  const start = addDays(today, -Number(range));
  const summary = useQuery({
    queryKey: ["youtube", range],
    queryFn: () => api.get<YoutubeSummary>(`/api/media/youtube/summary${qs({ start, end: today, limit: 60 })}`),
  });
  const upload = useMutation({
    mutationFn: (f: File) => api.upload<HistoryImport>("/api/media/youtube/takeout", f),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["youtube"] });
      qc.invalidateQueries({ queryKey: ["youtube-stats"] });
      invalidate();
      toast(
        r.source === "chrome_history"
          ? `${num(r.new_events)} new videos from Chrome's history (of ${num(r.events_in_file)})${r.channel_lookup ? " · looking up their channels…" : ""}`
          : `${num(r.new_events)} new videos (of ${num(r.events_in_file)}) · ${num(r.estimated_blocks)} estimated blocks in the ledger`,
        "good",
      );
    },
  });
  // Chrome's lines come without channel: OwnLife finds them in the background.
  const stats = useQuery({
    queryKey: ["youtube-stats"],
    queryFn: () => api.get<YoutubeStats>("/api/media/youtube/stats"),
    refetchInterval: (q) => (q.state.data?.channel_lookup.state === "running" ? 2000 : false),
  });
  const lookup = stats.data?.channel_lookup;
  const lookupRan = useRef(false);
  useEffect(() => {
    if (lookup?.state === "running") lookupRan.current = true;
    else if (lookupRan.current) {
      lookupRan.current = false;
      qc.invalidateQueries({ queryKey: ["youtube"] });
      invalidate();
    }
  }, [lookup?.state, qc, invalidate]);
  const classify = useMutation({
    mutationFn: async ({ channel, category_id }: { channel: string; category_id: number }) => {
      await api.post("/api/rules", { field: "channel", pattern: channel, category_id });
      return api.post<{ youtube_blocks_rebuilt: number }>("/api/rules/reapply");
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["youtube"] });
      invalidate();
      toast("Rule created; history reclassified", "good");
    },
  });
  const s = summary.data;
  const kinds = s ? chartKinds(s.hours_by_kind as Partial<Record<Kind, number>>) : null;
  return (
    <div className="space-y-5">
      <MeasuredToday />
      <BlockingCard />
      <SortingCard />
      <Card
        title="YouTube history"
        subtitle="The past, from Google Takeout: YouTube's history (YouTube → history, JSON), or Chrome's (Chrome → History.json, the recent weeks in that browser). Durations are estimated from the gaps between videos."
        action={
          <>
            <input ref={fileRef} type="file" accept=".json,.html" hidden onChange={(e) => { if (e.target.files?.[0]) upload.mutate(e.target.files[0]); e.target.value = ""; }} />
            <Button variant="primary" icon={<FileUp size={15} />} onClick={() => fileRef.current?.click()} loading={upload.isPending}>
              Import a history
            </Button>
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <Tabs value={range} onChange={setRange} tabs={RANGES} />
          <span className="text-[13px] text-ink-3">From now on, on this laptop: the YouTube extension measures real minutes (Settings → Integrations).</span>
        </div>
        {lookup?.state === "running" && (
          <p className="mt-3 flex items-center gap-2 text-[13px] text-ink-2">
            <Spinner /> Finding the channels of Chrome's videos: {num(lookup.done)} of {num(lookup.total)}. The channel rules apply when it is done.
          </p>
        )}
        {lookup?.state === "error" && (
          <p className="mt-3 text-[13px] text-warning">The channel lookup stopped ({lookup.error}). Import the file again to finish it.</p>
        )}
        <div className="mt-3"><ErrorNote error={upload.error} /></div>
      </Card>
      {!s ? (
        <Spinner />
      ) : s.videos === 0 ? (
        <Card><Empty icon={<MonitorPlay size={24} />} title="No YouTube history in this range">Import a history file above.</Empty></Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Videos opened" value={compactNum(s.videos)} sub={`${shortDate(s.start)} – ${shortDate(s.end)}`} />
            <StatTile
              label="Hours"
              value={num(s.estimated_hours, s.estimated_hours < 10 ? 1 : 0)}
              sub={s.measured_hours ? `${num(s.measured_hours, 1)} measured, the rest estimated` : `≈ ${(s.estimated_hours / Math.max(1, Number(range))).toFixed(1)} h/day, estimated`}
            />
            <StatTile label="Channels" value={num(s.channels_count)} />
            <StatTile label="Unclassified channels" value={num(s.unclassified_channels)} sub="give them a category below" />
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <Card title="By kind" subtitle="Estimated hours">
              <HBars
                rows={Object.entries(kinds!).filter(([, v]) => v > 0).map(([k, v]) => ({ key: k, label: KIND_LABEL[k as Kind], value: v, color: kindColor(k) }))}
                format={(v) => `${v.toFixed(1)}h`}
              />
            </Card>
            <Card title="Hour of day" subtitle="When the watching happens (estimated hours)">
              <Columns
                title="Hours"
                data={s.hours_of_day.map((v, i) => ({ label: i % 3 === 0 ? `${i}h` : "", value: v, tip: `${String(i).padStart(2, "0")}:00` }))}
                format={(v) => v.toFixed(1)}
                unit="h"
                height={170}
              />
            </Card>
          </div>
          {s.hours_by_month.length > 1 && (
            <Card title="By month" subtitle="Estimated hours">
              <Columns
                title="Hours"
                data={s.hours_by_month.map((m) => ({ label: m.month.slice(2), value: m.hours, tip: m.month }))}
                format={(v) => v.toFixed(0)}
                unit="h"
                labelEvery={Math.ceil(s.hours_by_month.length / 12)}
              />
            </Card>
          )}
          <Card title="Channels" subtitle="Pick a category for a channel: a rule is created and the history reclassified">
            <div className="scroll-thin max-h-[560px] overflow-auto">
              <table className="w-full text-[13px]">
                <thead className="sticky top-0 bg-panel-solid">
                  <tr className="text-left text-ink-3">
                    <th className="py-2 font-medium">Channel</th>
                    <th className="py-2 text-right font-medium">Videos</th>
                    <th className="py-2 text-right font-medium">Hours</th>
                    <th className="py-2 pl-4 font-medium">Category</th>
                  </tr>
                </thead>
                <tbody>
                  {s.channels.map((c) => (
                    <tr key={c.channel} className="border-t border-line">
                      <td className="py-2 text-ink">{c.channel}</td>
                      <td className="num py-2 text-right text-ink-2">{c.videos}</td>
                      <td className="num py-2 text-right text-ink">{c.hours.toFixed(1)}</td>
                      <td className="py-2 pl-4">
                        {c.category ? (
                          <span className="inline-flex items-center gap-2">
                            <span className="h-2.5 w-2.5 rounded-full" style={{ background: kindColor(c.category.kind) }} />
                            {c.category.name}
                          </span>
                        ) : (
                          <CategorySelect value={null} onChange={(id) => id && classify.mutate({ channel: c.channel, category_id: id })} className="h-8 max-w-[220px]" />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

const STATUS: { id: MediaItem["status"]; label: string }[] = [
  { id: "in_progress", label: "In progress" },
  { id: "want", label: "Want" },
  { id: "done", label: "Done" },
  { id: "dropped", label: "Dropped" },
];

function LibraryTab() {
  const qc = useQueryClient();
  const items = useQuery({ queryKey: ["media"], queryFn: () => api.get<MediaItem[]>("/api/media") });
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ kind: "book", title: "", creator: "", status: "want", category_id: null as number | null, progress_total: "", progress_unit: "pages" });
  const update = useMutation({
    mutationFn: ({ id, ...body }: { id: number } & Partial<MediaItem>) => api.patch(`/api/media/${id}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["media"] }),
  });
  const create = useMutation({
    mutationFn: () => api.post("/api/media", { ...f, creator: f.creator || null, progress_total: f.progress_total ? Number(f.progress_total) : null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["media"] });
      setAdding(false);
    },
  });
  if (!items.data) return <Spinner />;
  return (
    <div className="space-y-5">
      <div className="flex justify-end"><Button variant="primary" icon={<Plus size={15} />} onClick={() => setAdding(true)}>Add</Button></div>
      <div className="grid gap-5 xl:grid-cols-2">
        {STATUS.map((st) => {
          const list = items.data!.filter((m) => m.status === st.id);
          return (
            <Card key={st.id} title={st.label} subtitle={`${list.length}`}>
              {list.length ? (
                <ul className="divide-y divide-line">
                  {list.map((m) => (
                    <li key={m.id} className="flex items-center gap-3 py-2.5">
                      <span className="text-ink-3">{m.kind === "book" ? <BookOpen size={16} /> : <Clapperboard size={16} />}</span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-ink">{m.title}</p>
                        <p className="truncate text-[12px] text-ink-3">
                          {m.creator ?? m.kind}
                          {m.progress_current !== null && ` · ${m.progress_current}${m.progress_total ? ` / ${m.progress_total}` : ""} ${m.progress_unit ?? ""}`}
                        </p>
                      </div>
                      <Badge>{m.kind}</Badge>
                      <Select value={m.status} onChange={(e) => update.mutate({ id: m.id, status: e.target.value as MediaItem["status"] })} className="h-8 w-32">
                        {STATUS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                      </Select>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[13px] text-ink-3">Nothing here.</p>
              )}
            </Card>
          );
        })}
      </div>
      <Modal open={adding} onClose={() => setAdding(false)} title="Add to the library">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Kind">
              <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
                {["book", "course", "series", "anime", "manga", "movie", "channel", "podcast", "article"].map((k) => <option key={k}>{k}</option>)}
              </Select>
            </Field>
            <Field label="Status">
              <Select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
                {STATUS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </Select>
            </Field>
          </div>
          <Field label="Title"><Input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required /></Field>
          <Field label="Author / channel"><Input value={f.creator} onChange={(e) => setF({ ...f, creator: e.target.value })} /></Field>
          <Field label="Category"><CategorySelect value={f.category_id} onChange={(v) => setF({ ...f, category_id: v })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Length"><Input type="number" value={f.progress_total} onChange={(e) => setF({ ...f, progress_total: e.target.value })} /></Field>
            <Field label="Unit"><Input value={f.progress_unit} onChange={(e) => setF({ ...f, progress_unit: e.target.value })} /></Field>
          </div>
          <ErrorNote error={create.error} />
          <div className="flex justify-end"><Button type="submit" variant="primary" loading={create.isPending}>Add</Button></div>
        </form>
      </Modal>
    </div>
  );
}

export function MediaPage() {
  const [tab, setTab] = useState<"youtube" | "library">("youtube");
  return (
    <div>
      <PageHeader
        title="Watching & reading"
        subtitle="What goes in. Signal and noise, by the numbers."
        actions={<Tabs value={tab} onChange={setTab} tabs={[{ id: "youtube", label: "YouTube" }, { id: "library", label: "Library" }]} />}
      />
      {tab === "youtube" ? <YouTubeTab /> : <LibraryTab />}
    </div>
  );
}
