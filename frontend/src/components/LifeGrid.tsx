// A life in weeks. One column per year of age, 52 rows per column (the week of
// the year counted from the birthday). Lived weeks are filled; weeks with logged
// time are shaded by core hours (one hue, light → dark: magnitude); the current
// week is the one amber cell. Chapters sit in a strip above; selecting one keeps
// its weeks bright and dims the rest (emphasis), instead of spending nine hues.

import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { useMemo, useState } from "react";
import { addDays, longDate, num, shortDate } from "../lib/format";
import type { Chapter, LifeEvent, WeekCell } from "../lib/types";
import { Legend, useWidth } from "./charts";

const WEEKS = 52;
const GAP = 2;
const BINS = [0, 2, 8, 20, 40]; // core hours per week → --seq-1..5

function addYears(iso: string, years: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const target = new Date(Date.UTC(y + years, m - 1, d));
  if (target.getUTCMonth() !== m - 1) target.setUTCDate(0); // 29 Feb → 28 Feb
  return target.toISOString().slice(0, 10);
}

function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
}

/** (year of age, week of that year) for a date. */
function cellOf(birth: string, date: string): [number, number] {
  let year = Number(date.slice(0, 4)) - Number(birth.slice(0, 4));
  if (date < addYears(birth, year)) year -= 1;
  const weekIdx = Math.floor(daysBetween(addYears(birth, year), date) / 7);
  return [year, Math.min(WEEKS - 1, weekIdx)];
}

function cellStart(birth: string, year: number, week: number): string {
  return addDays(addYears(birth, year), week * 7);
}

function seqStep(coreHours: number): number {
  let step = 0;
  for (let i = 0; i < BINS.length; i++) if (coreHours > BINS[i]) step = i + 1;
  return step;
}

interface Props {
  birth: string;
  years: number;
  today: string;
  awakening?: string | null;
  chapters: Chapter[];
  events?: LifeEvent[];
  weeks: Record<string, WeekCell>;
}

export function LifeGrid({ birth, years, today, awakening, chapters, events = [], weeks }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<{ y: number; w: number } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const left = 28;
  const step = width > 0 ? Math.max(6, Math.min(14, Math.floor((width - left) / years))) : 10;
  const cell = step - GAP;
  const gridW = years * step;
  const gridH = WEEKS * step;
  const [ty, tw] = cellOf(birth, today);
  const awakeCell = awakening ? cellOf(birth, awakening) : null;

  // Aggregate the backend's week index (days since birth / 7) into grid cells.
  const data = useMemo(() => {
    const map = new Map<string, { core: number; noise: number; logged: number; journal: number }>();
    for (const [idx, w] of Object.entries(weeks)) {
      const date = addDays(birth, Number(idx) * 7);
      const [y, wk] = cellOf(birth, date);
      const key = `${y}:${wk}`;
      const cur = map.get(key) ?? { core: 0, noise: 0, logged: 0, journal: 0 };
      const h = w.hours ?? {};
      cur.core += h.core ?? 0;
      cur.noise += (h.noise ?? 0) + (h.destructive ?? 0);
      cur.logged += Object.values(h).reduce((a, v) => a + (v ?? 0), 0);
      cur.journal += w.journal_days;
      map.set(key, cur);
    }
    return map;
  }, [weeks, birth]);

  // Life events, by grid cell: an amber ring marks the week of each moment.
  const eventCells = useMemo(() => {
    const map = new Map<string, LifeEvent[]>();
    for (const e of events) {
      if (e.date < birth) continue;
      const [y, w] = cellOf(birth, e.date);
      const key = `${y}:${w}`;
      map.set(key, [...(map.get(key) ?? []), e]);
    }
    return map;
  }, [events, birth]);

  const chapterOf = (date: string): Chapter | undefined =>
    [...chapters]
      .sort((a, b) => b.start_date.localeCompare(a.start_date))
      .find((c) => c.start_date <= date && (!c.end_date || date <= c.end_date));

  // Chapter strip: lanes so that overlapping chapters do not hide each other.
  const lanes = useMemo(() => {
    const out: { c: Chapter; lane: number; x0: number; x1: number }[] = [];
    const ends: number[] = [];
    for (const c of [...chapters].sort((a, b) => a.start_date.localeCompare(b.start_date))) {
      const x0 = (daysBetween(birth, c.start_date) / 365.2425) * step;
      const endDate = c.end_date ?? (c.kind === "plan" ? addYears(birth, years) : today);
      const x1 = Math.max(x0 + 3, (daysBetween(birth, endDate) / 365.2425) * step);
      let lane = ends.findIndex((e) => e <= x0 - 1);
      if (lane === -1) {
        lane = ends.length;
        ends.push(x1);
      } else ends[lane] = x1;
      out.push({ c, lane, x0, x1 });
    }
    return { items: out, count: Math.max(1, ends.length) };
  }, [chapters, birth, step, years, today]);

  const laneH = 22;
  const stripH = lanes.count * (laneH + 4);
  const selectedChapter = chapters.find((c) => c.id === selected);

  function cellFill(y: number, w: number): { fill: string; opacity: number } {
    const start = cellStart(birth, y, w);
    const isPast = y < ty || (y === ty && w < tw);
    const d = data.get(`${y}:${w}`);
    let fill = "var(--grid)";
    let opacity = 0.55;
    if (y === ty && w === tw) return { fill: "var(--amber)", opacity: 1 };
    if (d && d.logged > 0) {
      const s = seqStep(d.core);
      fill = s > 0 ? `var(--seq-${s})` : "var(--ink-3)";
      opacity = s > 0 ? 1 : 0.5;
    } else if (isPast) {
      fill = "var(--ink-3)";
      opacity = 0.32;
    }
    if (selectedChapter) {
      const inside = start >= selectedChapter.start_date && (!selectedChapter.end_date || start <= selectedChapter.end_date);
      if (!inside) opacity *= 0.22;
      else if (!isPast && !(d && d.logged)) {
        fill = "var(--accent)";
        opacity = 0.35;
      }
    }
    return { fill, opacity };
  }

  const cells = [];
  const dots = [];
  for (let y = 0; y < years; y++) {
    for (let w = 0; w < WEEKS; w++) {
      const { fill, opacity } = cellFill(y, w);
      cells.push(
        <rect
          key={`${y}:${w}`}
          x={left + y * step}
          y={stripH + 8 + w * step}
          width={cell}
          height={cell}
          rx={Math.min(2, cell / 4)}
          fill={fill}
          opacity={opacity}
          className={y === ty && w === tw ? "animate-pulse-soft" : undefined}
        />,
      );
      if ((data.get(`${y}:${w}`)?.journal ?? 0) > 0 && !(y === ty && w === tw)) {
        dots.push(
          <circle
            key={`d${y}:${w}`}
            cx={left + y * step + cell / 2}
            cy={stripH + 8 + w * step + cell / 2}
            r={Math.max(1.2, cell / 5)}
            fill="var(--accent)"
            pointerEvents="none"
          />,
        );
      }
    }
  }
  const order = [...chapters].sort((a, b) => a.start_date.localeCompare(b.start_date));
  const numberOf = (id: number) => order.findIndex((c) => c.id === id) + 1;

  const hoverInfo = (() => {
    if (!hover) return null;
    const start = cellStart(birth, hover.y, hover.w);
    const d = data.get(`${hover.y}:${hover.w}`);
    const ch = chapterOf(start);
    const index = Math.floor(daysBetween(birth, start) / 7);
    return { start, d, ch, index, events: eventCells.get(`${hover.y}:${hover.w}`) ?? [] };
  })();

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const x = e.clientX - rect.left - left;
    const yy = e.clientY - rect.top - stripH - 8;
    const y = Math.floor(x / step);
    const w = Math.floor(yy / step);
    if (y >= 0 && y < years && w >= 0 && w < WEEKS) setHover({ y, w });
    else setHover(null);
  }

  return (
    <div>
      <div ref={ref} className="relative w-full overflow-x-auto scroll-thin" onMouseLeave={() => setHover(null)}>
        <svg
          width={Math.max(gridW + left + 4, 200)}
          height={stripH + 8 + gridH + 26}
          onMouseMove={onMove}
          role="img"
          aria-label={`Life in weeks: ${years} years, current week highlighted`}
        >
          {lanes.items.map(({ c, lane, x0, x1 }) => {
            const w = x1 - x0;
            const active = selected === c.id;
            return (
              <g
                key={c.id}
                transform={`translate(${left + x0}, ${lane * (laneH + 4)})`}
                onClick={() => setSelected(active ? null : c.id)}
                className="cursor-pointer"
                tabIndex={0}
                role="button"
                aria-pressed={active}
                aria-label={`${c.title}${c.kind === "plan" ? " (plan)" : ""}`}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setSelected(active ? null : c.id)}
                style={{ outline: "none" }}
              >
                <rect
                  width={Math.max(3, w - GAP)}
                  height={laneH}
                  rx={6}
                  fill={active ? "var(--accent-soft)" : c.kind === "plan" ? "var(--amber-soft)" : "var(--panel-hover)"}
                  stroke={active ? "var(--accent)" : "transparent"}
                />
                {w > c.title.length * 6.2 + 30 ? (
                  <text x={8} y={laneH / 2} dy="0.34em" fontSize={11} fill={active ? "var(--ink)" : "var(--ink-2)"}>
                    {numberOf(c.id)} · {c.title}
                  </text>
                ) : (
                  w >= 14 && (
                    <text x={(w - GAP) / 2} y={laneH / 2} dy="0.34em" fontSize={10} textAnchor="middle" fill={active ? "var(--ink)" : "var(--ink-2)"}>
                      {numberOf(c.id)}
                    </text>
                  )
                )}
                <title>{`${c.title}${c.approximate ? " (approximate dates)" : ""}`}</title>
              </g>
            );
          })}

          {cells}
          {dots}
          {[...eventCells.keys()].map((key) => {
            const [y, w] = key.split(":").map(Number);
            return (
              <rect
                key={`e${key}`}
                x={left + y * step - 1.5}
                y={stripH + 8 + w * step - 1.5}
                width={cell + 3}
                height={cell + 3}
                rx={3}
                fill="none"
                stroke="var(--amber)"
                strokeWidth={1.5}
                pointerEvents="none"
              />
            );
          })}

          {awakeCell && (
            <rect
              x={left + awakeCell[0] * step - 2}
              y={stripH + 8 + awakeCell[1] * step - 2}
              width={cell + 4}
              height={cell + 4}
              rx={3}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={1.5}
              pointerEvents="none"
            />
          )}

          {hover && (
            <rect
              x={left + hover.y * step - 1}
              y={stripH + 8 + hover.w * step - 1}
              width={cell + 2}
              height={cell + 2}
              rx={3}
              fill="none"
              stroke="var(--ink)"
              strokeWidth={1.5}
              pointerEvents="none"
            />
          )}

          {Array.from({ length: Math.floor((years - 1) / 10) + 1 }, (_, i) => i * 10).map((age) => (
            <text
              key={age}
              x={left + age * step + cell / 2}
              y={stripH + 8 + gridH + 16}
              fontSize={11}
              textAnchor="middle"
              fill="var(--ink-3)"
              className="num"
            >
              {age}
            </text>
          ))}
          <text x={0} y={stripH + 8 + cell} fontSize={10} fill="var(--ink-3)">wk 1</text>
          <text x={0} y={stripH + 8 + gridH - 2} fontSize={10} fill="var(--ink-3)">52</text>
        </svg>

        <AnimatePresence>
          {hover && hoverInfo && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.12 }}
              className="glass-solid pointer-events-none absolute z-20 w-[230px] rounded-xl px-3 py-2.5 text-[12px] shadow-xl"
              style={{
                left: Math.min(left + hover.y * step + 14, Math.max(0, gridW + left - 236)),
                top: stripH + 8 + Math.min(hover.w * step, gridH - 120),
              }}
            >
              <p className="font-semibold text-ink">
                Week {num(hoverInfo.index + 1)} · age {hover.y}
              </p>
              <p className="text-ink-3">
                {shortDate(hoverInfo.start)} – {shortDate(addDays(hoverInfo.start, 6))} {hoverInfo.start.slice(0, 4)}
              </p>
              {hoverInfo.ch && <p className="mt-1 text-ink-2">{hoverInfo.ch.title}</p>}
              {hoverInfo.events.map((e) => (
                <p key={e.id} className={clsx("mt-1 font-medium text-amber", e.is_private && "private-blur")}>◆ {e.title}</p>
              ))}
              {hoverInfo.d && hoverInfo.d.logged > 0 && (
                <div className="mt-1.5 space-y-0.5">
                  <Row label="Core" value={`${hoverInfo.d.core.toFixed(1)}h`} color="var(--k-core)" />
                  <Row label="Noise" value={`${hoverInfo.d.noise.toFixed(1)}h`} color="var(--k-noise)" />
                  <Row label="Logged" value={`${hoverInfo.d.logged.toFixed(1)}h`} />
                </div>
              )}
              {hoverInfo.d && hoverInfo.d.journal > 0 && (
                <p className="mt-1 text-ink-3">{hoverInfo.d.journal} journal day{hoverInfo.d.journal > 1 ? "s" : ""}</p>
              )}
              {hover.y === ty && hover.w === tw && <p className="mt-1 font-medium text-amber">This week</p>}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <Legend
          items={[
            { key: "lived", label: "Lived", color: "color-mix(in oklab, var(--ink-3) 45%, transparent)" },
            { key: "s1", label: "Recorded: < 2h core", color: "var(--seq-1)" },
            { key: "s3", label: "8–20h", color: "var(--seq-3)" },
            { key: "s5", label: "40h+ core / week", color: "var(--seq-5)" },
            { key: "now", label: "This week", color: "var(--amber)" },
            { key: "journal", label: "Journal written (dot)", color: "var(--accent)" },
            { key: "event", label: "A life event (ring)", color: "var(--amber)" },
            { key: "ahead", label: "Ahead", color: "var(--grid)" },
          ]}
        />
        {selectedChapter && (
          <button onClick={() => setSelected(null)} className={clsx("text-[12px] text-accent hover:underline")}>
            Showing “{selectedChapter.title}” — clear
          </button>
        )}
      </div>
      {awakening && (
        <p className="mt-2 text-[12px] text-ink-3">
          The cyan ring marks the Awakening — {longDate(awakening)}.
        </p>
      )}
      {order.length > 0 && (
        <ol className="mt-4 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
          {order.map((c) => {
            const active = selected === c.id;
            return (
              <li key={c.id}>
                <button
                  onClick={() => setSelected(active ? null : c.id)}
                  className={clsx(
                    "flex w-full items-baseline gap-2 rounded-lg px-2.5 py-1.5 text-left text-[12px] transition",
                    active ? "bg-accent-soft text-ink" : "text-ink-2 hover:bg-panel-hover",
                  )}
                >
                  <span className="num w-4 shrink-0 text-ink-3">{numberOf(c.id)}</span>
                  <span className="min-w-0 flex-1 truncate">{c.title}</span>
                  <span className="num shrink-0 text-ink-3">
                    {c.start_date.slice(0, 4)}–{c.end_date ? c.end_date.slice(0, 4) : c.kind === "plan" ? "…" : "now"}
                    {c.kind === "plan" && " · plan"}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="inline-flex items-center gap-1.5 text-ink-2">
        {color && <span className="h-0.5 w-3 rounded-full" style={{ background: color }} />}
        {label}
      </span>
      <span className="num font-semibold text-ink">{value}</span>
    </div>
  );
}
