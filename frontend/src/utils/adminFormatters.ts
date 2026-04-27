export function formatWinRate(winRate: number | null | undefined): string {
  return `${((winRate ?? 0) * 100).toFixed(1)}%`;
}

export function isBuySide(exchangeSide: "Buy" | "Sell"): boolean {
  return exchangeSide === "Buy";
}
