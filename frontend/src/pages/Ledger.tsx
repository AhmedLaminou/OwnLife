import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { DayComposition, HBars, StackedDays } from "../components/charts";
import { DayTimeline } from "../components/DayTimeline";
import { EntryModal, KindDot } from "../components/domain";
import { Badge, Button, Card, Empty, IconButton, Input, PageHeader, Spinner, StatTile, Tabs } from "../components/ui";
import { api, qs } from "../lib/api";
import { addDays, clock, hm, localDate, longDate, minutesInto, shortDate } from "../lib/format";
import { useNow, useTimeZone, useToday } from "../lib/hooks";
import { chartKinds, kindColor } from "../lib/kinds";
import type { DayView, RangeStats, TimeEntry } from "../lib/types";

const SOURCE_LABEL: Record<string, string> = {
  manual: "manual",
  timer: "timer",
  quick: "quick-log",
  ai: "assistant",
  journal: "journal",
  window: "window tracker",
  activitywatch: "ActivityWatch",
  extension: "YouTube, measured",
  youtube_takeout: "YouTube history",
};

const SEEN_BY: Record<string, string> = {
  window: "The window tracker",
  activitywatch: "ActivityWatch",
  extension: "The YouTube extension",
  youtube_takeout: "Your YouTube or Chrome history",
};

function DayMode({ day, setDay }: { day: string; setDay: (d: string) => void }) {
  const tz = useTimeZone();
  const today = useToday();
  const now = useNow(60_000);
  const [editing, setEditing] = useState<TimeEntry | null>(null);
  const [creating, setCreating] = useState(false);
  const { data, isFetching } = useQuery({ queryKey: ["day", day], queryFn: () => api.get<DayView>(`/api/time/day/${day}`) });
  const isToday = day === today;
  const nowMinute = isToday ? minutesInto(now.toISOString(), day, tz) : null;
  const elapsed = isToday ? (nowMinute ?? 0) * 60 : 86400;

  const blocks = useMemo(
    () =>
      (data?.entries ?? []).map((e) => ({
        id: e.id,
        start: minutesInto(e.started_at, day, tz),
        end: e.ended_at ? minutesInto(e.ended_at, day, tz) : (nowMinute ?? 1440),
        title: e.title,
        kind: e.kind,
        sub: `${e.category?.name ?? "uncategorised"} · ${hm(e.duration_seconds)}`,
        estimate: e.is_estimate,
        running: e.running,
      })),
    [data, day, tz, nowMinute],
  );

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <IconButton label="Previous day" onClick={() => setDay(addDays(day, -1))}>
          <ChevronLeft size={18} />
        </IconButton>
        <Input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} className="w-44" />
        <IconButton label="Next day" onClick={() => setDay(addDays(day, 1))}>
          <ChevronRight size={18} />
        </IconButton>
        {!isToday && (
          <Button size="sm" variant="ghost" onClick={() => setDay(today)}>
            Today
          </Button>
        )}
        <span className="ml-1 text-sm text-ink-3">{longDate(day)}</span>
        <div className="flex-1" />
        <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreating(true)}>
          Log time
        </Button>
      </div>

      {!data ? (
        <div className="grid h-64 place-items-center"><Spinner /></div>
      ) : (
        <div className={`grid gap-5 xl:grid-cols-[minmax(320px,1fr)_1.4fr] ${isFetching ? "opacity-70 transition-opacity" : ""}`}>
          <Card title="Timeline" subtitle={isToday ? `Now ${clock(now, tz)}` : undefined}>
            {data.entries.length ? (
              <DayTimeline blocks={blocks} nowMinute={nowMinute} onSelect={(id) => setEditing(data.entries.find((e) => e.id === id) ?? null)} height={620} />
            ) : (
              <Empty title="Nothing logged on this day">Use “Log time”, the timer, or Capture.</Empty>
            )}
          </Card>
          <div className="space-y-5">
            <div className="grid grid-cols-3 gap-3">
              <StatTile label="Logged" value={hm(data.covered_seconds)} />
              <StatTile label="Not logged" value={hm(data.untracked_seconds)} sub={isToday ? "so far today" : "of 24h"} />
              <StatTile label="Core" value={hm(chartKinds(data.totals_by_kind).core)} sub={`target ${data.targets.focus_hours}h`} />
            </div>
            {data.overlaps.length > 0 && (
              <Card title="Worth a look" subtitle="An automatic record disagrees with one of your own entries: one of the two is wrong">
                <ul className="space-y-2 text-[13px]">
                  {data.overlaps.map((o) => {
                    const auto = data.entries.find((e) => e.id === o.entry_id);
                    const mine = data.entries.find((e) => e.id === o.covered_by);
                    if (!auto || !mine) return null;
                    return (
                      <li key={`${o.entry_id}-${o.covered_by}`} className="flex items-start gap-2 text-ink-2">
                        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning" />
                        <span>
                          {SEEN_BY[auto.source] ?? auto.source} saw <button className="text-ink hover:underline" onClick={() => setEditing(auto)}>“{auto.title}”</button>{" "}
                          {clock(o.start, tz)}–{clock(o.end, tz)} ({hm(o.seconds)}), during your{" "}
                          <button className="text-ink hover:underline" onClick={() => setEditing(mine)}>“{mine.title}”</button>.{" "}
                          {o.counted === "automatic"
                            ? "A timer cannot see you switch, so the measured minutes are the ones counted. Pause the timer next time."
                            : "Your entry is the one counted."}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </Card>
            )}
            <Card title="The day by kind">
              <DayComposition kinds={data.totals_by_kind} elapsedSeconds={elapsed} />
            </Card>
            {data.totals_by_category.length > 0 && (
              <Card title="By category">
                <HBars
                  rows={data.totals_by_category.map((c) => ({
                    key: String(c.category_id ?? "none"),
                    label: c.name,
                    value: c.seconds,
                    color: kindColor(c.kind),
                  }))}
                />
              </Card>
            )}
            {data.entries.length > 0 && (
              <Card title="Entries">
                <ul className="divide-y divide-line">
                  {data.entries.map((e) => (
                    <li key={e.id}>
                      <button onClick={() => setEditing(e)} className="flex w-full items-center gap-3 py-2.5 text-left transition hover:bg-panel-hover/50">
                        <KindDot kind={e.kind} />
                        <span className="num w-[92px] shrink-0 text-[13px] text-ink-3">
                          {clock(e.started_at, tz)}–{e.ended_at ? clock(e.ended_at, tz) : "now"}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{e.title}</span>
                        {e.people.length > 0 && <span className="hidden text-[12px] text-ink-3 md:inline">with {e.people.map((p) => p.name).join(", ")}</span>}
                        {e.source !== "manual" && <Badge>{SOURCE_LABEL[e.source] ?? e.source}</Badge>}
                        {e.is_estimate && <Badge tone="warning">estimate</Badge>}
                        {e.counted_seconds < e.window_seconds - 60 && (
                          <span title="The rest of this time is already covered by a more exact source (your own entries first): it is not counted twice.">
                            <Badge>{e.counted_seconds > 0 ? `${hm(e.counted_seconds)} counted` : "already counted"}</Badge>
                          </span>
                        )}
                        <span className="num w-14 shrink-0 text-right text-[13px] font-medium">{hm(e.duration_seconds)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </div>
      )}
      <EntryModal open={creating || !!editing} entry={editing} defaultDay={day} onClose={() => { setEditing(null); setCreating(false); }} />
    </>
  );
}

const PRESETS = [
  { id: "7", label: "7 days" },
  { id: "30", label: "30 days" },
  { id: "90", label: "90 days" },
  { id: "365", label: "1 year" },
] as const;

function RangeMode() {
  const today = useToday();
  const [preset, setPreset] = useState<string>("30");
  const end = today;
  const start = addDays(today, -(Number(preset) - 1));
  const { data, isFetching } = useQuery({
    queryKey: ["stats", start, end],
    queryFn: () => api.get<RangeStats>(`/api/time/stats${qs({ start, end })}`),
  });
  if (!data) return <div className="grid h-64 place-items-center"><Spinner /></div>;
  const totals = chartKinds(data.totals_by_kind);
  const avg = chartKinds(data.avg_by_kind_per_tracked_day);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={preset} onChange={setPreset} tabs={PRESETS.map((p) => ({ id: p.id, label: p.label }))} />
        <span className="text-sm text-ink-3">
          {shortDate(start)} – {shortDate(end)}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Tracked days" value={`${data.tracked_days} / ${data.days.length}`} sub="days with ≥ 2h logged" />
        <StatTile label="Core per tracked day" value={hm(avg.core)} sub={`target ${data.targets.focus_hours}h`} />
        <StatTile label="Noise per tracked day" value={hm(avg.noise)} sub={`budget ${data.targets.noise_budget_hours}h`} />
        <StatTile label="Core, total" value={hm(totals.core)} sub={`noise total ${hm(totals.noise)}`} />
      </div>
      <Card title="Day by day" subtitle="Stacked by kind">
        <StackedDays days={data.days.map((d) => ({ date: d.date, kinds: d.kinds }))} height={260} fading={isFetching} />
      </Card>
      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Top categories">
          {data.totals_by_category.length ? (
            <HBars
              rows={data.totals_by_category.slice(0, 12).map((c) => ({
                key: String(c.category_id ?? "none"),
                label: c.name,
                value: c.seconds,
                color: kindColor(c.kind),
              }))}
            />
          ) : (
            <Empty title="Nothing logged in this range" />
          )}
        </Card>
        <Card title="Top activities">
          {data.top_titles.length ? (
            <HBars
              rows={data.top_titles.map((t, i) => ({ key: `${i}`, label: t.title, value: t.seconds, color: "var(--k-core)" }))}
            />
          ) : (
            <Empty title="Nothing logged in this range" />
          )}
        </Card>
      </div>
    </div>
  );
}

export function LedgerPage() {
  const tz = useTimeZone();
  const [mode, setMode] = useState<"day" | "range">("day");
  const [day, setDay] = useState(() => localDate(new Date(), tz));
  return (
    <div>
      <PageHeader
        title="Ledger"
        subtitle="Where the hours actually went"
        actions={<Tabs value={mode} onChange={setMode} tabs={[{ id: "day", label: "Day" }, { id: "range", label: "Range" }]} />}
      />
      {mode === "day" ? <DayMode day={day} setDay={setDay} /> : <RangeMode />}
    </div>
  );
}
