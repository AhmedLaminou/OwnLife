import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Clapperboard, FileUp, MonitorPlay, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Columns, HBars } from "../components/charts";
import { CategorySelect, useInvalidateLedger } from "../components/domain";
import { Badge, Button, Card, Empty, ErrorNote, Field, Input, Modal, PageHeader, Select, Spinner, StatTile, Tabs, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import { addDays, clock, compactNum, hm, num, shortDate } from "../lib/format";
import { useTimeZone, useToday } from "../lib/hooks";
import { KIND_LABEL, chartKinds, kindColor } from "../lib/kinds";
import type { HistoryImport, Kind, MeasuredToday as MeasuredTodayData, MediaItem, YoutubeStats, YoutubeSummary } from "../lib/types";

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
