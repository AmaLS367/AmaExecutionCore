function parseNumericValue(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : value;
  }
  if (value === "Infinity") {
    return Number.POSITIVE_INFINITY;
  }
  const parsed = Number.parseFloat(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export function formatWinRate(winRate: number | string | null | undefined): string {
  const numericValue = parseNumericValue(winRate);
  if (numericValue === null) {
    return "—";
  }
  return `${(numericValue * 100).toFixed(1)}%`;
}

export function formatProfitFactor(value: number | string | null | undefined): string {
  const numericValue = parseNumericValue(value);
  if (numericValue === null) {
    return "—";
  }
  if (numericValue === Number.POSITIVE_INFINITY) {
    return "∞";
  }
  return numericValue.toFixed(2);
}

export function formatDecimal(value: number | string | null | undefined, digits = 2): string {
  const numericValue = parseNumericValue(value);
  if (numericValue === null) {
    return "—";
  }
  if (numericValue === Number.POSITIVE_INFINITY) {
    return "∞";
  }
  return numericValue.toFixed(digits);
}

export function formatCurrency(value: number | string | null | undefined, digits = 2): string {
  const numericValue = parseNumericValue(value);
  if (numericValue === null) {
    return "—";
  }
  if (numericValue === Number.POSITIVE_INFINITY) {
    return "∞";
  }
  return `${numericValue >= 0 ? "$" : "-$"}${Math.abs(numericValue).toFixed(digits)}`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "—";
  }
  return value.toString();
}

export function isBuySide(exchangeSide: "Buy" | "Sell"): boolean {
  return exchangeSide === "Buy";
}
