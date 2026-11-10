import { describe, expect, it } from "vitest";
import { greeting, initials, money, pct, relativeDay, timeAgo } from "./format";

describe("format utils", () => {
  it("formats money and percent", () => {
    expect(money(24580, "USD")).toBe("$24,580.00");
    expect(money(5000, "INR")).toContain("5,000.00");
    expect(pct(12.8)).toBe("+12.8%");
    expect(pct(-3)).toBe("-3.0%");
    expect(pct(null)).toBe("—");
  });
  it("initials and greeting", () => {
    expect(initials("Sarah Johnson")).toBe("SJ");
    expect(initials(null)).toBe("?");
    expect(greeting(new Date(2026, 0, 1, 9))).toBe("Good morning");
    expect(greeting(new Date(2026, 0, 1, 20))).toBe("Good evening");
  });
  it("relative dates", () => {
    expect(relativeDay(new Date().toISOString())).toBe("Today");
    expect(timeAgo(Date.now() - 12_000)).toBe("12s ago");
    expect(timeAgo(Date.now() - 3 * 3600_000)).toBe("3h ago");
  });
});
