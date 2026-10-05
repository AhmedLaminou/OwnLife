// Charts built to one spec: bars ≤ 24px with a 4px rounded data end and a square
// baseline, 2px surface gaps between touching marks, hairline solid gridlines,
// text in text tokens (never in series colours), a legend whenever there are two
// or more series, a hover/focus tooltip on every mark, and a table view twin.

import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { hm, shortDate, weekday } from "../lib/format";
import { CHART_KINDS, KIND_LABEL, chartKinds, kindColor } from "../lib/kinds";
import type { KindSeconds } from "../lib/types";

const GAP = 2;
const RADIUS = 4;

// ---------------------------------------------------------------- shared helpers
/** Width of an element, kept current. A callback ref, so it also works for an
 *  element that mounts after the first render (e.g. after an empty state). */
export function useWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [node, setNode] = useState<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!node) return;
    const ro = new ResizeObserver((entries) => setWidth(Math.floor(entries[0].contentRect.width)));
    ro.observe(node);
    return () => ro.disconnect();
  }, [node]);
  return [setNode, width];
}

const TOP = 10; // room above the highest tick so its label is not clipped

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 4, 5, 8, 10]) if (m * exp >= v) return m * exp;
  return 10 * exp;
}

/** A rect with only its top corners rounded: the data end is rounded, the baseline square. */
function topRoundedPath(x: number, y: number, w: number, h: number, r = RADIUS): string {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} L${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} L${x + w},${y + h} Z`;
}

export interface LegendItem {
  key: string;
  label: string;
  color: string;
  value?: string;
}

export function Legend({ items, className }: { items: LegendItem[]; className?: string }) {
  return (
    <ul className={clsx("flex flex-wrap gap-x-4 gap-y-1.5 text-[12px]", className)}>
      {items.map((it) => (
        <li key={it.key} className="inline-flex items-center gap-1.5 text-ink-2">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: it.color }} aria-hidden />
          <span>{it.label}</span>
          {it.value && <span className="num text-ink-3">{it.value}</span>}
        </li>
      ))}
    </ul>
  );
}

export interface TipRow {
  label: string;
  value: string;
  color?: string;
}

function Tooltip({ x, y, title, rows, containerWidth }: { x: number; y: number; title: string; rows: TipRow[]; containerWidth: number }) {
  const left = Math.min(Math.max(x + 12, 4), Math.max(4, containerWidth - 196));
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.12 }}
      className="glass-solid pointer-events-none absolute z-20 w-[190px] rounded-xl px-3 py-2 text-[12px] shadow-xl"
      style={{ left, top: Math.max(0, y - 10) }}
    >
      <p className="mb-1 text-ink-3">{title}</p>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-3 py-0.5">
          <span className="inline-flex items-center gap-1.5 text-ink-2">
            {r.color && <span className="h-0.5 w-3 rounded-full" style={{ background: r.color }} aria-hidden />}
            {r.label}
          </span>
          <span className="num font-semibold text-ink">{r.value}</span>
        </div>
      ))}
    </motion.div>
  );
}

/** Chart ⇄ table switch. Every chart's values stay readable without hovering. */
export function ChartShell({ legend, table, children, fading }: { legend?: ReactNode; table: ReactNode; children: ReactNode; fading?: boolean }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <div className={clsx("transition-opacity", fading && "opacity-60")}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">{legend}</div>
        <button
          onClick={() => setAsTable((v) => !v)}
          className="shrink-0 rounded-lg px-2 py-1 text-[12px] text-ink-3 transition hover:bg-panel-hover hover:text-ink-2"
        >
          {asTable ? "Chart" : "Table"}
        </button>
      </div>
      {asTable ? <div className="scroll-thin max-h-[320px] overflow-auto">{table}</div> : children}
    </div>
  );
}

export function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="text-left text-ink-3">
          {head.map((h, i) => (
            <th key={h} className={clsx("pb-2 font-medium", i > 0 && "text-right")}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-line">
            {r.map((c, j) => (
              <td key={j} className={clsx("py-1.5", j > 0 ? "num text-right text-ink" : "text-ink-2")}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- stacked day columns
export function StackedDays({
  days,
  height = 200,
  fading,
}: {
  days: { date: string; kinds: KindSeconds }[];
  height?: number;
  fading?: boolean;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const series = [...CHART_KINDS, "uncategorized"] as const;
  const data = useMemo(() => days.map((d) => ({ date: d.date, k: chartKinds(d.kinds) })), [days]);
  const present = series.filter((s) => data.some((d) => d.k[s] > 0));
  const maxH = niceMax(Math.max(1, ...data.map((d) => series.reduce((a, s) => a + d.k[s], 0) / 3600)));
  const left = 34;
  const bottom = 22;
  const plotH = height - bottom;
  const band = data.length ? Math.max(1, (width - left) / data.length) : 0;
  const barW = Math.min(24, Math.max(4, band * 0.62));
  const y = (h: number) => TOP + (plotH - TOP) * (1 - h / maxH);
  const ticks = [0, maxH / 2, maxH];
  const labelEvery = Math.max(1, Math.ceil(data.length / Math.max(1, Math.floor((width - left) / 52))));

  const legend = (
    <Legend
      items={present.map((s) => ({
        key: s,
        label: KIND_LABEL[s],
        color: kindColor(s),
        value: hm(data.reduce((a, d) => a + d.k[s], 0)),
      }))}
    />
  );
  const table = (
    <DataTable
      head={["Day", ...present.map((s) => KIND_LABEL[s]), "Total"]}
      rows={data.map((d) => [
        `${weekday(d.date)} ${shortDate(d.date)}`,
        ...present.map((s) => hm(d.k[s])),
        hm(series.reduce((a, s) => a + d.k[s], 0)),
      ])}
    />
  );

  if (!present.length) {
    return <p className="py-12 text-center text-sm text-ink-3">Nothing logged on these days yet.</p>;
  }

  return (
    <ChartShell legend={legend} table={table} fading={fading}>
      <div ref={ref} className="relative" style={{ height }} onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={height} role="img" aria-label="Hours per day by kind">
            {ticks.map((t) => (
              <g key={t}>
                <line x1={left} x2={width} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
                <text x={left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--ink-3)" className="num">
                  {Number.isInteger(t) ? t : t.toFixed(1)}h
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = left + band * i + band / 2;
              let acc = 0;
              const segs = series
                .filter((s) => d.k[s] > 0)
                .map((s) => {
                  const h0 = acc;
                  acc += d.k[s] / 3600;
                  return { s, y0: y(acc), y1: y(h0) };
                });
              return (
                <g
                  key={d.date}
                  tabIndex={0}
                  onMouseEnter={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  aria-label={`${d.date}: ${hm(series.reduce((a, s) => a + d.k[s], 0))}`}
                  style={{ outline: "none" }}
                >
                  <rect x={cx - band / 2} y={0} width={band} height={plotH} fill="transparent" />
                  {segs.map((g, j) => {
                    // the 2px gap sits under every segment but the first
                    const top = g.y0;
                    const h = Math.max(0, g.y1 - g.y0 - (j > 0 ? GAP : 0));
                    const isTop = j === segs.length - 1;
                    return isTop ? (
                      <motion.path
                        key={g.s}
                        d={topRoundedPath(cx - barW / 2, top, barW, h)}
                        fill={kindColor(g.s)}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: hover === null || hover === i ? 1 : 0.55 }}
                        transition={{ duration: 0.25, delay: hover === null ? i * 0.012 : 0 }}
                      />
                    ) : (
                      <motion.rect
                        key={g.s}
                        x={cx - barW / 2}
                        y={top}
                        width={barW}
                        height={h}
                        fill={kindColor(g.s)}
                        initial={{ opacity: 0 }}
                        animate={{ opacity: hover === null || hover === i ? 1 : 0.55 }}
                        transition={{ duration: 0.25, delay: hover === null ? i * 0.012 : 0 }}
                      />
                    );
                  })}
                  {i % labelEvery === 0 && (
                    <text x={cx} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--ink-3)">
                      {data.length <= 10 ? weekday(d.date) : shortDate(d.date)}
                    </text>
                  )}
                </g>
              );
            })}
            <line x1={left} x2={width} y1={plotH} y2={plotH} stroke="var(--axis)" strokeWidth={1} />
          </svg>
        )}
        <AnimatePresence>
          {hover !== null && data[hover] && (
            <Tooltip
              x={left + band * hover + band / 2}
              y={8}
              containerWidth={width}
              title={`${weekday(data[hover].date)} ${shortDate(data[hover].date)}`}
              rows={[
                ...series
                  .filter((s) => data[hover].k[s] > 0)
                  .reverse()
                  .map((s) => ({ label: KIND_LABEL[s], value: hm(data[hover].k[s]), color: kindColor(s) })),
                { label: "Total", value: hm(series.reduce((a, s) => a + data[hover].k[s], 0)) },
              ]}
            />
          )}
        </AnimatePresence>
      </div>
    </ChartShell>
  );
}

// ---------------------------------------------------------------- 24h composition
export function DayComposition({ kinds, elapsedSeconds }: { kinds: KindSeconds; elapsedSeconds?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const k = chartKinds(kinds);
  const series = [...CHART_KINDS, "uncategorized"] as const;
  const total = 86400;
  const logged = series.reduce((a, s) => a + k[s], 0);
  const untracked = Math.max(0, (elapsedSeconds ?? total) - logged);
  const h = 16;
  let x = 0;
  const segs = series
    .filter((s) => k[s] > 0)
    .map((s) => {
      const w = Math.max(2, (k[s] / total) * width);
      const seg = { s, x, w };
      x += w;
      return seg;
    });
  const legend = (
    <Legend
      items={[
        ...segs.map((g) => ({ key: g.s, label: KIND_LABEL[g.s], color: kindColor(g.s), value: hm(k[g.s]) })),
        ...(untracked > 0 ? [{ key: "untracked", label: "Not logged", color: "var(--grid)", value: hm(untracked) }] : []),
      ]}
    />
  );
  const table = (
    <DataTable
      head={["Kind", "Time", "Share of 24h"]}
      rows={[
        ...segs.map((g) => [KIND_LABEL[g.s], hm(k[g.s]), `${Math.round((k[g.s] / total) * 100)}%`]),
        ["Not logged (so far)", hm(untracked), `${Math.round((untracked / total) * 100)}%`],
      ]}
    />
  );
  return (
    <ChartShell legend={legend} table={table}>
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        <svg width={width || 1} height={h + 4} role="img" aria-label="Share of the 24 hours by kind">
          <rect x={0} y={0} width={width} height={h} rx={h / 2} fill="var(--grid)" />
          {segs.map((g, i) => (
            <motion.rect
              key={g.s}
              x={g.x + (i > 0 ? GAP : 0)}
              y={0}
              height={h}
              rx={i === 0 ? h / 2 : 3}
              fill={kindColor(g.s)}
              initial={{ width: 0 }}
              animate={{ width: Math.max(0, g.w - (i > 0 ? GAP : 0)), opacity: hover && hover !== g.s ? 0.55 : 1 }}
              transition={{ duration: 0.7, delay: i * 0.05, ease: [0.22, 1, 0.36, 1] }}
              onMouseEnter={() => setHover(g.s)}
              tabIndex={0}
              onFocus={() => setHover(g.s)}
              onBlur={() => setHover(null)}
              aria-label={`${KIND_LABEL[g.s]}: ${hm(k[g.s])}`}
              style={{ outline: "none" }}
            />
          ))}
        </svg>
        <AnimatePresence>
          {hover && (
            <Tooltip
              x={(segs.find((g) => g.s === hover)?.x ?? 0) + 8}
              y={-56}
              containerWidth={width}
              title="Today"
              rows={[{ label: KIND_LABEL[hover as keyof typeof KIND_LABEL], value: hm(k[hover as keyof typeof k]), color: kindColor(hover) }]}
            />
          )}
        </AnimatePresence>
      </div>
    </ChartShell>
  );
}

// ---------------------------------------------------------------- hour-by-hour strip
export interface StripEntry {
  id: number;
  title: string;
  kind: string;
  start: number; // minutes since local midnight
  end: number;
  label: string; // "06:10–08:40"
}

/** The day as it happened: entries at their real clock times, with "now". */
export function DayStrip({ entries, nowMinute }: { entries: StripEntry[]; nowMinute: number | null }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<StripEntry | null>(null);
  const h = 18;
  const x = (m: number) => (Math.min(1440, Math.max(0, m)) / 1440) * width;
  const table = (
    <DataTable head={["When", "What", "Kind"]} rows={entries.map((e) => [e.label, e.title, KIND_LABEL[e.kind as keyof typeof KIND_LABEL] ?? e.kind])} />
  );
  return (
    <ChartShell table={table}>
      <div ref={ref} className="relative" onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={h + 20} role="img" aria-label="Today hour by hour">
            <rect x={0} y={0} width={width} height={h} rx={5} fill="var(--grid)" />
            {entries.map((e, i) => {
              const x0 = x(e.start);
              const w = Math.max(2, x(e.end) - x0 - GAP);
              return (
                <motion.rect
                  key={e.id}
                  x={x0}
                  y={0}
                  height={h}
                  rx={3}
                  fill={kindColor(e.kind)}
                  initial={{ width: 0 }}
                  animate={{ width: w, opacity: hover && hover.id !== e.id ? 0.5 : 1 }}
                  transition={{ duration: 0.5, delay: Math.min(i * 0.03, 0.4) }}
                  onMouseEnter={() => setHover(e)}
                  onFocus={() => setHover(e)}
                  onBlur={() => setHover(null)}
                  tabIndex={0}
                  aria-label={`${e.label} ${e.title}`}
                  style={{ outline: "none" }}
                />
              );
            })}
            {nowMinute !== null && <rect x={x(nowMinute) - 1} y={-2} width={2} height={h + 4} rx={1} fill="var(--amber)" />}
            {[0, 6, 12, 18, 24].map((t) => (
              <text key={t} x={(t / 24) * width} y={h + 15} fontSize={11} fill="var(--ink-3)" textAnchor={t === 0 ? "start" : t === 24 ? "end" : "middle"} className="num">
                {String(t).padStart(2, "0")}:00
              </text>
            ))}
          </svg>
        )}
        <AnimatePresence>
          {hover && (
            <Tooltip
              x={x(hover.start)}
              y={-60}
              containerWidth={width}
              title={hover.label}
              rows={[{ label: KIND_LABEL[hover.kind as keyof typeof KIND_LABEL] ?? hover.kind, value: hover.title.slice(0, 22), color: kindColor(hover.kind) }]}
            />
          )}
        </AnimatePresence>
      </div>
    </ChartShell>
  );
}

// ---------------------------------------------------------------- horizontal bars
export function HBars({
  rows,
  format = hm,
  max,
  legend,
}: {
  rows: { key: string; label: ReactNode; value: number; color: string; sub?: ReactNode }[];
  format?: (v: number) => string;
  max?: number;
  legend?: ReactNode;
}) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  const table = <DataTable head={["Item", "Value"]} rows={rows.map((r) => [typeof r.label === "string" ? r.label : r.key, format(r.value)])} />;
  if (!rows.length) return <p className="py-8 text-center text-sm text-ink-3">Nothing to show yet.</p>;
  return (
    <ChartShell legend={legend} table={table}>
      <ul className="space-y-3">
        {rows.map((r, i) => (
          <li key={r.key} className="min-w-0">
            <div className="mb-1 flex items-baseline justify-between gap-3">
              <span className="truncate text-[13px] text-ink-2">{r.label}</span>
              <span className="num shrink-0 text-[13px] font-medium text-ink">{format(r.value)}</span>
            </div>
            <svg width="100%" height={10} className="block overflow-visible" preserveAspectRatio="none" aria-hidden>
              <motion.rect
                height={10}
                rx={4}
                fill={r.color}
                initial={{ width: "0%" }}
                animate={{ width: `${Math.max(1.5, (r.value / top) * 100)}%` }}
                transition={{ duration: 0.6, delay: i * 0.03, ease: [0.22, 1, 0.36, 1] }}
              />
            </svg>
            {r.sub && <div className="mt-0.5 text-[11px] text-ink-3">{r.sub}</div>}
          </li>
        ))}
      </ul>
    </ChartShell>
  );
}

// ---------------------------------------------------------------- single-series columns
export function Columns({
  data,
  height = 160,
  color = "var(--k-core)",
  format = (v: number) => v.toFixed(1),
  unit = "",
  labelEvery = 1,
  title,
  selected = null,
  onSelect,
}: {
  data: { label: string; value: number; tip?: string }[];
  height?: number;
  color?: string;
  format?: (v: number) => string;
  unit?: string;
  labelEvery?: number;
  title: string;
  /** The bar shown as chosen (the others dim). */
  selected?: number | null;
  /** Makes the bars clickable (and Enter on a focused bar). */
  onSelect?: (index: number) => void;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const left = 34;
  const bottom = 22;
  const plotH = height - bottom;
  const maxV = niceMax(Math.max(0.0001, ...data.map((d) => d.value)));
  const band = data.length ? (width - left) / data.length : 0;
  const barW = Math.min(24, Math.max(3, band - GAP));
  const y = (v: number) => TOP + (plotH - TOP) * (1 - v / maxV);
  const table = <DataTable head={[title, "Value"]} rows={data.map((d) => [d.tip ?? d.label, `${format(d.value)}${unit}`])} />;
  return (
    <ChartShell table={table}>
      <div ref={ref} className="relative" style={{ height }} onMouseLeave={() => setHover(null)}>
        {width > 0 && (
          <svg width={width} height={height} role="img" aria-label={title}>
            {[0, maxV / 2, maxV].map((t) => (
              <g key={t}>
                <line x1={left} x2={width} y1={y(t)} y2={y(t)} stroke="var(--grid)" />
                <text x={left - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--ink-3)" className="num">
                  {format(t)}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = left + band * i + band / 2;
              const h = Math.max(0, plotH - y(d.value));
              return (
                <g
                  key={i}
                  tabIndex={0}
                  onMouseEnter={() => setHover(i)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  onClick={onSelect ? () => onSelect(i) : undefined}
                  onKeyDown={onSelect ? (e) => e.key === "Enter" && onSelect(i) : undefined}
                  role={onSelect ? "button" : undefined}
                  aria-pressed={onSelect ? selected === i : undefined}
                  aria-label={`${d.tip ?? d.label}: ${format(d.value)}${unit}`}
                  style={{ outline: "none", cursor: onSelect ? "pointer" : undefined }}
                >
                  <rect x={cx - band / 2} y={0} width={band} height={plotH} fill="transparent" />
                  {h > 0 && (
                    <motion.path
                      d={topRoundedPath(cx - barW / 2, plotH - h, barW, h)}
                      fill={color}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: hover === i || (hover === null && (selected === null || selected === i)) ? 1 : 0.45 }}
                      transition={{ duration: 0.3, delay: hover === null ? i * 0.01 : 0 }}
                    />
                  )}
                  {i % labelEvery === 0 && (
                    <text x={cx} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--ink-3)">
                      {d.label}
                    </text>
                  )}
                </g>
              );
            })}
            <line x1={left} x2={width} y1={plotH} y2={plotH} stroke="var(--axis)" />
          </svg>
        )}
        <AnimatePresence>
          {hover !== null && data[hover] && (
            <Tooltip
              x={left + band * hover + band / 2}
              y={8}
              containerWidth={width}
              title={data[hover].tip ?? data[hover].label}
              rows={[{ label: title, value: `${format(data[hover].value)}${unit}`, color }]}
            />
          )}
        </AnimatePresence>
      </div>
    </ChartShell>
  );
}

// ---------------------------------------------------------------- habit calendar
const STATUS_STYLE: Record<string, { fill: string; label: string; opacity?: number }> = {
  done: { fill: "var(--good)", label: "Done" },
  missed: { fill: "var(--critical)", label: "Missed", opacity: 0.75 },
  skip: { fill: "var(--ink-3)", label: "Skipped", opacity: 0.5 },
  clean: { fill: "var(--good)", label: "Clean", opacity: 0.55 },
  urge: { fill: "var(--warning)", label: "Urge resisted" },
  relapse: { fill: "var(--critical)", label: "Relapse" },
  pending: { fill: "var(--accent)", label: "Today (open)", opacity: 0.35 },
};

export function HeatCalendar({ days, cell = 12 }: { days: { date: string; status: string | null }[]; cell?: number }) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  if (!days.length) return null;
  const first = new Date(`${days[0].date}T12:00:00Z`);
  const offset = (first.getUTCDay() + 6) % 7; // Monday first
  const step = cell + GAP;
  const cols = Math.ceil((days.length + offset) / 7);
  const width = cols * step;
  const height = 7 * step;
  const used = Array.from(new Set(days.map((d) => d.status).filter((s): s is string => !!s && s in STATUS_STYLE)));
  return (
    <div className="relative" onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label="Habit calendar" className="max-w-full">
        {days.map((d, i) => {
          const idx = i + offset;
          const x = Math.floor(idx / 7) * step;
          const y = (idx % 7) * step;
          const st = d.status ? STATUS_STYLE[d.status] : undefined;
          return (
            <rect
              key={d.date}
              x={x}
              y={y}
              width={cell}
              height={cell}
              rx={3}
              fill={st ? st.fill : "var(--grid)"}
              opacity={st?.opacity ?? 1}
              tabIndex={0}
              onMouseEnter={() => setHover({ i, x, y })}
              onFocus={() => setHover({ i, x, y })}
              onBlur={() => setHover(null)}
              aria-label={`${d.date}: ${st?.label ?? "no data"}`}
              style={{ outline: "none" }}
            />
          );
        })}
      </svg>
      <Legend className="mt-2" items={used.map((s) => ({ key: s, label: STATUS_STYLE[s].label, color: STATUS_STYLE[s].fill }))} />
      <AnimatePresence>
        {hover && (
          <Tooltip
            x={hover.x}
            y={hover.y - 46}
            containerWidth={Math.max(width, 220)}
            title={`${weekday(days[hover.i].date)} ${shortDate(days[hover.i].date)}`}
            rows={[{ label: "Status", value: STATUS_STYLE[days[hover.i].status ?? ""]?.label ?? "—" }]}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

