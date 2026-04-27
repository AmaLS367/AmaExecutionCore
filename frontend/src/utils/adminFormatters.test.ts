import { describe, expect, it } from "vitest";
import { formatWinRate, isBuySide } from "./adminFormatters";

describe("adminFormatters", () => {
  it("formats backend win_rate fraction as percentage", () => {
    expect(formatWinRate(0.6)).toBe("60.0%");
    expect(formatWinRate(1)).toBe("100.0%");
    expect(formatWinRate(0)).toBe("0.0%");
    expect(formatWinRate(null)).toBe("0.0%");
    expect(formatWinRate(undefined)).toBe("0.0%");
  });

  it("matches backend exchange_side enum casing", () => {
    expect(isBuySide("Buy")).toBe(true);
    expect(isBuySide("Sell")).toBe(false);
  });
});
