import { describe, expect, it } from "vitest";
import { addDays, fromLocalInput, hm, localDate, minutesInto, money, toLocalInput } from "../lib/format";
import { chartKinds } from "../lib/kinds";

describe("durations", () => {
  it("formats hours and minutes the way the ledger shows them", () => {
    expect(hm(0)).toBe("0m");
    expect(hm(45 * 60)).toBe("45m");
    expect(hm(2 * 3600)).toBe("2h");
    expect(hm(2 * 3600 + 5 * 60)).toBe("2h05");
  });
});

describe("local days in Niamey (UTC+1)", () => {
  const tz = "Africa/Niamey";
  it("puts 23:30 UTC on the next local day", () => {
    expect(localDate(new Date("2026-09-10T23:30:00Z"), tz)).toBe("2026-09-11");
  });
  it("measures minutes from local midnight, across midnight too", () => {
    expect(minutesInto("2026-09-10T08:00:00Z", "2026-09-10", tz)).toBe(9 * 60);
    expect(minutesInto("2026-09-11T00:30:00Z", "2026-09-10", tz)).toBe(1440 + 90);
  });
  it("round-trips datetime-local inputs through the profile zone", () => {
    const iso = fromLocalInput("2026-09-10T09:00", tz);
    expect(iso).toBe("2026-09-10T08:00:00.000Z");
    expect(toLocalInput(iso, tz)).toBe("2026-09-10T09:00");
  });
  it("adds days across month ends", () => {
    expect(addDays("2026-08-31", 1)).toBe("2026-09-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });
});

describe("kinds", () => {
  it("folds the private 'destructive' kind into noise for charts", () => {
    const k = chartKinds({ core: 3600, noise: 600, destructive: 1200 });
    expect(k.noise).toBe(1800);
    expect(k.core).toBe(3600);
    expect("destructive" in k).toBe(false);
  });
});

describe("money", () => {
  it("writes FCFA for XOF", () => {
    expect(money(1500, "XOF")).toBe("1,500 FCFA");
  });
});
