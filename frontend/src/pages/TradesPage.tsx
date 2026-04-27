import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { tradesApi } from "../api/trades";
import { statsApi } from "../api/stats";

export function TradesPage() {
  const [page, setPage] = useState(1);
  const [symbol, setSymbol] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  const { data: summary } = useQuery({
    queryKey: ["trades-summary"],
    queryFn: statsApi.getSummary,
  });

  const { data: tradesRes, isLoading } = useQuery({
    queryKey: ["trades-list", page, symbol, fromDate, toDate],
    queryFn: () =>
      tradesApi.list({
        page,
        limit: 20,
        symbol: symbol || undefined,
        from_date: fromDate || undefined,
        to_date: toDate || undefined,
      }),
  });

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <SummaryCard title="Total Trades" value={summary?.total_trades.toString() || "0"} />
        <SummaryCard title="Win Rate" value={`${(summary?.win_rate || 0).toFixed(1)}%`} />
        <SummaryCard title="Profit Factor" value={(summary?.profit_factor || 0).toFixed(2)} />
        <SummaryCard title="Total P&L" value={`$${(summary?.total_pnl || 0).toFixed(2)}`} color={summary?.total_pnl && summary.total_pnl < 0 ? "text-red-400" : "text-emerald-400"} />
        <SummaryCard title="Avg Trade" value={`$${(summary?.avg_trade || 0).toFixed(2)}`} color={summary?.avg_trade && summary.avg_trade < 0 ? "text-red-400" : "text-emerald-400"} />
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden flex flex-col">
        {/* Filters */}
        <div className="px-5 py-4 border-b border-zinc-800 flex flex-wrap gap-4 items-end">
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">Symbol</label>
            <input
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="e.g. BTCUSDT"
              className="bg-zinc-950 border border-zinc-800 rounded px-3 py-1.5 text-sm text-zinc-100 focus:outline-none focus:border-emerald-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">From Date</label>
            <input
              type="date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 rounded px-3 py-1.5 text-sm text-zinc-100 focus:outline-none focus:border-emerald-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-zinc-400 mb-1">To Date</label>
            <input
              type="date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 rounded px-3 py-1.5 text-sm text-zinc-100 focus:outline-none focus:border-emerald-500"
            />
          </div>
          <button
            onClick={() => {
              setSymbol("");
              setFromDate("");
              setToDate("");
              setPage(1);
            }}
            className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded text-sm transition-colors"
          >
            Clear Filters
          </button>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-zinc-400 uppercase bg-zinc-900/50 border-b border-zinc-800">
              <tr>
                <th className="px-6 py-3">Symbol</th>
                <th className="px-6 py-3">Direction</th>
                <th className="px-6 py-3">Side</th>
                <th className="px-6 py-3">Status</th>
                <th className="px-6 py-3 text-right">Realized P&L</th>
                <th className="px-6 py-3 text-right">Closed At</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-zinc-500">Loading trades...</td>
                </tr>
              ) : !tradesRes?.items.length ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-zinc-500">No trades found</td>
                </tr>
              ) : (
                tradesRes.items.map((t) => (
                  <tr key={t.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/20">
                    <td className="px-6 py-4 font-medium">{t.symbol}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 rounded text-xs ${t.signal_direction === "long" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
                        {t.signal_direction.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-6 py-4">{t.exchange_side.toUpperCase()}</td>
                    <td className="px-6 py-4">
                      <span className="px-2 py-1 rounded bg-zinc-800 text-zinc-300 text-xs">
                        {t.status.toUpperCase()}
                      </span>
                    </td>
                    <td className={`px-6 py-4 text-right font-medium ${t.realized_pnl && t.realized_pnl < 0 ? "text-red-400" : t.realized_pnl && t.realized_pnl > 0 ? "text-emerald-400" : ""}`}>
                      {t.realized_pnl !== null ? `$${t.realized_pnl.toFixed(2)}` : "-"}
                    </td>
                    <td className="px-6 py-4 text-right text-zinc-400">
                      {t.closed_at ? new Date(t.closed_at).toLocaleString() : "-"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {tradesRes && tradesRes.pages > 1 && (
          <div className="px-5 py-4 border-t border-zinc-800 flex items-center justify-between">
            <div className="text-sm text-zinc-400">
              Showing page <span className="font-medium text-zinc-200">{tradesRes.page}</span> of <span className="font-medium text-zinc-200">{tradesRes.pages}</span> ({tradesRes.total} total)
            </div>
            <div className="flex gap-2">
              <button
                disabled={page === 1}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 border border-zinc-700 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed hover:bg-zinc-800"
              >
                Previous
              </button>
              <button
                disabled={page >= tradesRes.pages}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 border border-zinc-700 rounded text-sm disabled:opacity-50 disabled:cursor-not-allowed hover:bg-zinc-800"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SummaryCard({ title, value, color = "text-zinc-100" }: { title: string; value: string; color?: string }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <p className="text-xs font-medium text-zinc-400 mb-1">{title}</p>
      <p className={`text-xl font-bold ${color}`}>{value}</p>
    </div>
  );
}
