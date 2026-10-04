// Formatting: durations, dates (in the profile's time zone), numbers, money.

export function hm(seconds: number | undefined | null): string {
  const s = Math.max(0, Math.round(seconds ?? 0));
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  if (h === 0) return `${m}m`;
  if (m === 60) return `${h + 1}h`;
  return m ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`;
}

export function hours(seconds: number | undefined | null, digits = 1): string {
  return ((seconds ?? 0) / 3600).toFixed(digits);
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const whole = new Intl.NumberFormat("en");

export function num(n: number | undefined | null, digits = 0): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function compactNum(n: number): string {
  return Math.abs(n) >= 10000 ? compact.format(n) : whole.format(Math.round(n));
}

export function money(amount: number, currency: string): string {
  const label = currency === "XOF" || currency === "XAF" ? "FCFA" : currency;
  return `${whole.format(Math.round(amount))} ${label}`;
}

/** "2026-10-02" in the given zone — the local day of an instant. */
export function localDate(d: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function clock(iso: string | Date, timeZone: string): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}

/** Minutes since local midnight of `day` (can be negative or > 1440). */
export function minutesInto(iso: string, day: string, timeZone: string): number {
  const d = new Date(iso);
  const local = localDate(d, timeZone);
  const t = clock(d, timeZone);
  const [h, m] = t.split(":").map(Number);
  const dayDiff = Math.round((Date.parse(local) - Date.parse(day)) / 86400000);
  return dayDiff * 1440 + h * 60 + m;
}

export function addDays(day: string, n: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function longDate(day: string): string {
  return new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(`${day}T12:00:00Z`),
  );
}

export function shortDate(day: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${day}T12:00:00Z`));
}

export function weekday(day: string): string {
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: "UTC" }).format(new Date(`${day}T12:00:00Z`));
}

export function relativeDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 0) return days >= 60 ? `in ${Math.round(days / 30.4)} months` : `in ${days} days`;
  return `${-days} days ago`;
}

/** Local "YYYY-MM-DDTHH:MM" for <input type="datetime-local"> in the profile zone. */
export function toLocalInput(iso: string, timeZone: string): string {
  const d = new Date(iso);
  return `${localDate(d, timeZone)}T${clock(d, timeZone)}`;
}

/** The ISO instant of a local wall-clock time in the profile zone. */
export function fromLocalInput(value: string, timeZone: string): string {
  // Find the offset of the zone at that moment by formatting a guess, then correct.
  const [date, time] = value.split("T");
  const guess = new Date(`${date}T${time}:00Z`);
  const shown = new Date(`${localDate(guess, timeZone)}T${clock(guess, timeZone)}:00Z`);
  const offset = shown.getTime() - guess.getTime();
  return new Date(guess.getTime() - offset).toISOString();
}

export function pluralize(n: number, word: string, plural = `${word}s`): string {
  return `${num(n)} ${n === 1 ? word : plural}`;
}
