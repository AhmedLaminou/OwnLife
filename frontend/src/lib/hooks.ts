import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api";
import { localDate } from "./format";
import type { Category, Profile } from "./types";

export function useProfile() {
  return useQuery({ queryKey: ["profile"], queryFn: () => api.get<Profile>("/api/profile"), staleTime: 60_000 });
}

export function useCategories(includeArchived = false) {
  return useQuery({
    queryKey: ["categories", includeArchived],
    queryFn: () => api.get<Category[]>(`/api/categories${includeArchived ? "?include_archived=true" : ""}`),
    staleTime: 60_000,
  });
}

/** The profile's time zone (Africa/Niamey for Ahmed), falling back to the browser's. */
export function useTimeZone(): string {
  const { data } = useProfile();
  return data?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** A clock that re-renders every `ms` milliseconds. */
export function useNow(ms = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

export function useToday(): string {
  const tz = useTimeZone();
  const now = useNow(60_000);
  return localDate(now, tz);
}

const THEME_KEY = "ownlife.theme";

function readTheme(): "dark" | "light" {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    /* storage unavailable: fall through */
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function applyStoredTheme(): void {
  document.documentElement.dataset.theme = readTheme();
}

export function useTheme(): ["dark" | "light", () => void] {
  const [theme, setTheme] = useState<"dark" | "light">(readTheme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      window.localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* not persisted: fine */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}
