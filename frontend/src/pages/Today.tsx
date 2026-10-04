import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { BookOpen, Check, CircleSlash, Eye, Inbox, MoonStar, ShieldCheck, Sparkles, X } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { Link } from "react-router";
import { DayStrip, Legend, StackedDays } from "../components/charts";
import { CaptureModal, TimerCard, useInvalidateLedger } from "../components/domain";
import { NamedIcon } from "../components/icons";
import { Badge, Button, Card, Empty, Meter, Spinner, StatTile, useToast } from "../components/ui";
import { api } from "../lib/api";
import { clock, hm, longDate, minutesInto, relativeDays } from "../lib/format";
import { useNow, useTimeZone } from "../lib/hooks";
import { CHART_KINDS, KIND_LABEL, chartKinds, kindColor } from "../lib/kinds";
import type { Dashboard, DashboardHabit, DayView, PrayerDay } from "../lib/types";

function greeting(hour: number): string {
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function HabitRow({ h }: { h: DashboardHabit }) {
  const invalidate = useInvalidateLedger();
  const toast = useToast();
  const [revealed, setRevealed] = useState(false);
  const log = useMutation({
    mutationFn: (status: string) => api.post(`/api/habits/${h.id}/log`, { status }),
    onSuccess: (_d, status) => {
      invalidate();
      toast(status === "urge" ? "Urge resisted — that's the skill." : `${h.name}: ${status}`, "good");
    },
  });
  const auto = !!h.rule;
  const status = h.today;
  return (
    <li className="flex items-center gap-3 rounded-xl px-2 py-2 transition hover:bg-panel-hover">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-panel-hover text-ink-2">
        <NamedIcon name={h.icon} size={15} />
      </span>
      <div className={clsx("min-w-0 flex-1", h.is_private && "private-blur")} data-revealed={revealed}>
        <p className="truncate text-[13px] font-medium text-ink">{h.name}</p>
        <p className="text-[12px] text-ink-3">
          {h.kind === "quit"
            ? `${h.current_streak} clean day${h.current_streak === 1 ? "" : "s"}${h.next_milestone ? ` · next: ${h.next_milestone}` : ""}`
            : `streak ${h.current_streak} · best ${h.best_streak}${auto ? " · automatic" : ""}`}
        </p>
      </div>
      {h.is_private && (
        <button onClick={() => setRevealed((r) => !r)} className="text-ink-3 hover:text-ink-2" aria-label="Reveal">
          <Eye size={15} />
        </button>
      )}
      {h.kind === "quit" ? (
        <Button size="sm" variant="ghost" icon={<ShieldCheck size={14} />} onClick={() => log.mutate("urge")} loading={log.isPending}>
          Urge resisted
        </Button>
      ) : auto ? (
        <Badge tone={status === "done" ? "good" : status === "missed" ? "critical" : "neutral"}>
          {status === "done" ? <Check size={12} /> : status === "missed" ? <X size={12} /> : null}
          {status ?? "—"}
        </Badge>
      ) : status === "done" ? (
        <Badge tone="good">
          <Check size={12} /> done
        </Badge>
      ) : (
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" onClick={() => log.mutate("done")} icon={<Check size={14} />}>
            Done
          </Button>
          <Button size="sm" variant="ghost" onClick={() => log.mutate("missed")} aria-label="Missed" icon={<CircleSlash size={14} />} />
        </div>
      )}
    </li>
  );
}

/** "Asr 16:01 · in 1h12" — computed offline for the profile's city. */
function NextPrayer({ today }: { today: string }) {
  const { data } = useQuery({
    queryKey: ["prayer", today],
    queryFn: () => api.get<PrayerDay>(`/api/prayer/${today}`),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
  const next = data?.next;
  if (!next) return null;
  const h = Math.floor(next.minutes_left / 60);
  const m = next.minutes_left % 60;
  return (
    <Link
      to="/plan"
      className="glass inline-flex h-10 items-center gap-2 rounded-xl px-3 text-[13px] text-ink-2 transition hover:text-ink"
      title={`Prayer times for ${data.city} (${data.method_name}) — Settings → Rituals`}
    >
      <MoonStar size={15} className="text-amber" />
      <span className="font-medium text-ink">{next.label} {next.time}</span>
      <span className="text-ink-3">in {h ? `${h}h${String(m).padStart(2, "0")}` : `${m} min`}</span>
    </Link>
  );
}

export function TodayPage() {
  const tz = useTimeZone();
  const now = useNow(30_000);
  const [capture, setCapture] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<Dashboard>("/api/dashboard"),
    refetchInterval: 60_000,
  });
  const day = useQuery({
    queryKey: ["day", data?.today],
    queryFn: () => api.get<DayView>(`/api/time/day/${data!.today}`),
    enabled: !!data,
    refetchInterval: 60_000,
  });
  if (isLoading || !data) return <div className="grid h-64 place-items-center"><Spinner /></div>;

  const k = chartKinds(data.kinds_today);
  const core = k.core;
  const noise = k.noise;
  const target = data.targets.focus_hours * 3600;
  const stretch = data.targets.stretch_hours * 3600;
  const hour = Number(new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", hour12: false }).format(now));
  const minute = Number(new Intl.DateTimeFormat("en-GB", { timeZone: tz, minute: "2-digit" }).format(now));
  const elapsed = hour * 3600 + minute * 60;
  const pctOfTarget = target ? Math.round((core / target) * 100) : 0;
  const logged = day.data?.covered_seconds ?? 0;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-sm text-ink-3">
            {greeting(hour)}, {data.display_name}
            {data.mission_title && <span className="text-ink-2"> · {data.mission_title}</span>}
          </motion.p>
          <motion.h1
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl"
          >
            {data.awakening_day ? (
              <>
                Day <span className="text-accent">{data.awakening_day}</span>
                <span className="text-ink-3"> · </span>
              </>
            ) : null}
            {longDate(data.today)}
          </motion.h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {data.pending_drafts > 0 && (
            <Link to="/assistant?tab=inbox">
              <Button icon={<Inbox size={15} />}>
                {data.pending_drafts} draft{data.pending_drafts > 1 ? "s" : ""} to review
              </Button>
            </Link>
          )}
          <NextPrayer today={data.today} />
          <Button variant="primary" icon={<Sparkles size={15} />} onClick={() => setCapture(true)}>
            Capture
          </Button>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.35fr_1fr]">
        <Card title="Core work today" subtitle="Maths · physics · CS · AI · robotics · building">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <motion.p
              key={Math.round(core / 60)}
              initial={{ opacity: 0.4, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              className="text-[56px] font-semibold leading-none tracking-tight text-ink"
            >
              {hm(core)}
            </motion.p>
            <p className="pb-1.5 text-sm text-ink-3">
              {pctOfTarget}% of the {data.targets.focus_hours}h target · stretch {data.targets.stretch_hours}h
            </p>
          </div>
          <div className="mt-5">
            <Meter value={core} max={Math.max(stretch, core)} marker={target} label="Core work against target" height={12} />
            <div className="mt-1.5 flex justify-between text-[11px] text-ink-3">
              <span>0</span>
              <span>target {data.targets.focus_hours}h</span>
              <span>{data.targets.stretch_hours}h</span>
            </div>
          </div>
          <div className="mt-6">
            <p className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">Today, hour by hour</p>
            <Legend
              className="mb-3"
              items={[
                ...CHART_KINDS.filter((kk) => k[kk] > 0).map((kk) => ({ key: kk, label: KIND_LABEL[kk], color: kindColor(kk), value: hm(k[kk]) })),
                ...(k.uncategorized > 0 ? [{ key: "u", label: "Uncategorised", color: kindColor("uncategorized"), value: hm(k.uncategorized) }] : []),
              ]}
            />
            <DayStrip
              nowMinute={hour * 60 + minute}
              entries={(day.data?.entries ?? []).map((e) => ({
                id: e.id,
                title: e.title,
                kind: e.kind,
                start: minutesInto(e.started_at, data.today, tz),
                end: e.ended_at ? minutesInto(e.ended_at, data.today, tz) : hour * 60 + minute,
                label: `${clock(e.started_at, tz)}–${e.ended_at ? clock(e.ended_at, tz) : "now"}`,
              }))}
            />
            <p className="mt-2 text-[12px] text-ink-3">{hm(Math.max(0, elapsed - logged))} of the day so far is not logged.</p>
          </div>
        </Card>

        <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-1">
          <Card title="Timer" delay={0.05}>
            <TimerCard />
          </Card>
          <div className="grid grid-cols-2 gap-3">
            <StatTile label="Noise today" value={hm(noise)} sub={`budget ${data.targets.noise_budget_hours}h`}>
              <Meter
                value={noise}
                max={Math.max(data.targets.noise_budget_hours * 3600 * 2, noise)}
                marker={data.targets.noise_budget_hours * 3600}
                color={noise > data.targets.noise_budget_hours * 3600 ? "var(--critical)" : "var(--k-noise)"}
                label="Noise against budget"
              />
            </StatTile>
            <StatTile
              label="Journal"
              value={data.journal_today ? "Written" : "Not yet"}
              sub={data.journal_today ? "today's page exists" : "the Virtual Memory waits"}
            >
              <Link to="/journal" className="inline-flex items-center gap-1.5 text-[13px] text-accent hover:underline">
                <BookOpen size={14} /> Open journal
              </Link>
            </StatTile>
          </div>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.35fr_1fr]">
        <Card title="Last 7 days" subtitle="Hours logged per day, by kind" delay={0.08}>
          <StackedDays days={data.week} height={210} />
        </Card>
        <Card
          title="Habits"
          subtitle="Today"
          delay={0.1}
          action={
            <Link to="/habits" className="text-[13px] text-accent hover:underline">
              All habits
            </Link>
          }
        >
          {data.habits.length ? (
            <ul className="-mx-2 space-y-0.5">
              {data.habits.map((h) => (
                <HabitRow key={h.id} h={h} />
              ))}
            </ul>
          ) : (
            <Empty title="No habits yet" />
          )}
        </Card>
      </div>

      {data.milestones.length > 0 && (
        <Card title="Ahead" subtitle="Dated milestones" delay={0.12}>
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {data.milestones.map((m) => (
              <li key={m.id} className="rounded-xl border border-line p-3">
                <p className="text-[13px] font-medium text-ink">{m.title}</p>
                <p className="mt-1 text-[12px] text-ink-3">
                  {longDate(m.target_date)} · <span className="text-amber">{relativeDays(m.days_left)}</span>
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <CaptureModal open={capture} onClose={() => setCapture(false)} />
    </div>
  );
}
