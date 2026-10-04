// A vertical 24-hour column: blocks positioned by local minutes, overlapping
// blocks placed side by side in lanes. Used for the ledger and for plan vs. actual.

import clsx from "clsx";
import { motion } from "motion/react";
import { useEffect, useRef } from "react";
import { hm } from "../lib/format";
import { kindColor } from "../lib/kinds";
import type { Kind } from "../lib/types";

export interface TimelineBlock {
  id: string | number;
  start: number; // minutes since local midnight (may be < 0 or > 1440)
  end: number;
  title: string;
  kind: Kind;
  sub?: string;
  dim?: boolean;
  estimate?: boolean;
  running?: boolean;
}

const PX_PER_MIN = 0.75; // 45px per hour

function assignLanes(blocks: TimelineBlock[]): (TimelineBlock & { lane: number; lanes: number })[] {
  const sorted = [...blocks].sort((a, b) => a.start - b.start || b.end - a.end);
  const out: (TimelineBlock & { lane: number; lanes: number })[] = [];
  let cluster: (TimelineBlock & { lane: number; lanes: number })[] = [];
  let clusterEnd = -Infinity;
  const flush = () => {
    const n = Math.max(1, ...cluster.map((c) => c.lane + 1));
    cluster.forEach((c) => (c.lanes = n));
    out.push(...cluster);
    cluster = [];
  };
  for (const b of sorted) {
    if (b.start >= clusterEnd) {
      flush();
      clusterEnd = -Infinity;
    }
    const used = new Set(cluster.filter((c) => c.end > b.start).map((c) => c.lane));
    let lane = 0;
    while (used.has(lane)) lane++;
    cluster.push({ ...b, lane, lanes: 1 });
    clusterEnd = Math.max(clusterEnd, b.end);
  }
  flush();
  return out;
}

export function DayTimeline({
  blocks,
  onSelect,
  nowMinute,
  label,
  scrollToMinute = 5 * 60,
  height = 560,
}: {
  blocks: TimelineBlock[];
  onSelect?: (id: string | number) => void;
  nowMinute?: number | null;
  label?: string;
  scrollToMinute?: number;
  height?: number;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = Math.max(0, scrollToMinute * PX_PER_MIN - 20);
  }, [scrollToMinute]);
  const laid = assignLanes(
    blocks.map((b) => ({ ...b, start: Math.max(0, b.start), end: Math.min(1440, Math.max(b.end, b.start + 5)) })),
  );
  return (
    <div className="min-w-0">
      {label && <p className="mb-2 text-[12px] font-medium uppercase tracking-wider text-ink-3">{label}</p>}
      <div ref={scroller} className="scroll-thin relative overflow-y-auto rounded-xl" style={{ height }}>
        <div className="relative" style={{ height: 1440 * PX_PER_MIN }}>
          {Array.from({ length: 25 }, (_, h) => (
            <div key={h} className="absolute left-0 right-0 flex items-start" style={{ top: h * 60 * PX_PER_MIN }}>
              <span className="num w-11 -translate-y-1.5 pr-2 text-right text-[11px] text-ink-3">{String(h).padStart(2, "0")}:00</span>
              <span className="mt-0 h-px flex-1 bg-[var(--grid)]" />
            </div>
          ))}
          {nowMinute !== null && nowMinute !== undefined && nowMinute >= 0 && nowMinute <= 1440 && (
            <div className="absolute left-11 right-0 z-10 flex items-center" style={{ top: nowMinute * PX_PER_MIN }}>
              <span className="h-2 w-2 -translate-x-1 rounded-full bg-amber" />
              <span className="h-px flex-1 bg-amber" />
            </div>
          )}
          <div className="absolute bottom-0 left-12 right-1 top-0">
            {laid.map((b, i) => {
              const top = b.start * PX_PER_MIN;
              const h = Math.max(14, (b.end - b.start) * PX_PER_MIN - 2);
              const width = `calc(${100 / b.lanes}% - 3px)`;
              const leftPos = `calc(${(100 / b.lanes) * b.lane}%)`;
              const color = kindColor(b.kind);
              return (
                <motion.button
                  key={b.id}
                  type="button"
                  onClick={() => onSelect?.(b.id)}
                  initial={{ opacity: 0, x: -6 }}
                  animate={{ opacity: b.dim ? 0.45 : 1, x: 0 }}
                  transition={{ duration: 0.25, delay: Math.min(i * 0.02, 0.3) }}
                  className={clsx(
                    "absolute overflow-hidden rounded-lg text-left transition-[filter] hover:brightness-125",
                    onSelect ? "cursor-pointer" : "cursor-default",
                  )}
                  style={{
                    top,
                    height: h,
                    width,
                    left: leftPos,
                    background: `color-mix(in oklab, ${color} ${b.estimate ? 12 : 22}%, var(--panel-solid))`,
                    boxShadow: `inset 3px 0 0 ${color}`,
                  }}
                  title={`${b.title} · ${hm((b.end - b.start) * 60)}`}
                >
                  <div className="px-2.5 py-1">
                    <p className="truncate text-[12px] font-medium leading-tight text-ink">
                      {b.running && <span className="mr-1 inline-block h-1.5 w-1.5 animate-pulse-soft rounded-full bg-amber align-middle" />}
                      {b.title}
                    </p>
                    {h > 30 && b.sub && <p className="truncate text-[11px] text-ink-3">{b.sub}</p>}
                  </div>
                </motion.button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
