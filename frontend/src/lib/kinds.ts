import type { Kind, KindSeconds } from "./types";

// Chart series: eight kinds in the validated palette order. "destructive" is
// folded into "noise" in every chart — a ninth hue would break the palette, and
// keeping it inside noise is also the discreet choice.
export const CHART_KINDS = ["core", "growth", "work", "spirit", "social", "body", "maintenance", "noise"] as const;
export type ChartKind = (typeof CHART_KINDS)[number];

export const KIND_LABEL: Record<Kind, string> = {
  core: "Core mission",
  growth: "Growth",
  work: "Work",
  spirit: "Spirit",
  social: "Social",
  body: "Body",
  maintenance: "Maintenance",
  noise: "Noise",
  destructive: "Noise",
  uncategorized: "Uncategorised",
};

export const KIND_HINT: Record<Kind, string> = {
  core: "Maths, physics, CS, AI, robotics, building",
  growth: "Useful learning outside the core",
  work: "Job, internship",
  spirit: "Prayer, Quran, theology",
  social: "Family and friends",
  body: "Training, sport",
  maintenance: "Sleep, meals, hygiene, commute",
  noise: "Entertainment not chosen deliberately",
  destructive: "What you decided to quit",
  uncategorized: "Not classified yet",
};

export function kindColor(kind: Kind | string): string {
  const k = kind === "destructive" ? "noise" : kind;
  return `var(--k-${k})`;
}

/** Folds destructive into noise, keeps everything else. */
export function chartKinds(k: KindSeconds | undefined): Record<ChartKind | "uncategorized", number> {
  const out = { core: 0, growth: 0, work: 0, spirit: 0, social: 0, body: 0, maintenance: 0, noise: 0, uncategorized: 0 };
  if (!k) return out;
  for (const [key, v] of Object.entries(k)) {
    const target = key === "destructive" ? "noise" : key;
    if (target in out) out[target as keyof typeof out] += v ?? 0;
  }
  return out;
}
