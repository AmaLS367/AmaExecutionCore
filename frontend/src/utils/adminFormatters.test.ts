import { describe, expect, it } from "vitest";
import { formatProfitFactor, formatWinRate, isBuySide } from "./adminFormatters";

describe("adminFormatters", () => {
  it("formats backend win_rate fraction as percentage", () => {
    expect(formatWinRate(0.6)).toBe("60.0%");
    expect(formatWinRate("0.125")).toBe("12.5%");
    expect(formatWinRate(1)).toBe("100.0%");
    expect(formatWinRate(0)).toBe("0.0%");
    expect(formatWinRate(null)).toBe("—");
    expect(formatWinRate(undefined)).toBe("—");
  });

  it("formats profit factor safely for null and infinity", () => {
    expect(formatProfitFactor(null)).toBe("—");
    expect(formatProfitFactor(undefined)).toBe("—");
    expect(formatProfitFactor("Infinity")).toBe("∞");
    expect(formatProfitFactor(Number.POSITIVE_INFINITY)).toBe("∞");
    expect(formatProfitFactor("1.234")).toBe("1.23");
  });

  it("matches backend exchange_side enum casing", () => {
    expect(isBuySide("Buy")).toBe(true);
    expect(isBuySide("Sell")).toBe(false);
  });
});
