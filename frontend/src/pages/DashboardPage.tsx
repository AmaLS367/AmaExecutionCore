import { useQuery } from "@tanstack/react-query";
import { statsApi } from "../api/stats";
import { tradesApi } from "../api/trades";
import { EquityChart } from "../components/charts/EquityChart";
import { PnlChart } from "../components/charts/PnlChart";
import { Activity, DollarSign, Target, ShieldAlert } from "lucide-react";

export function DashboardPage() {
  const { data: stats } = useQuery({ queryKey: ["dashboard-stats"], queryFn: statsApi.getDashboard });
  const { data: equity } = useQuery({ queryKey: ["equity-curve"], queryFn: () => statsApi.getEquityCurve(30) });
  const { data: pnl } = useQuery({ queryKey: ["daily-pnl"], queryFn: () => statsApi.getDailyPnl(30) });
  const { data: openTrades } = useQuery({ queryKey: ["open-trades"], queryFn: tradesApi.getOpen });

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard title="Total Equity" value={`$${stats?.equity?.toFixed(2) || "0.00"}`} icon={<DollarSign className="text-zinc-400" size={20} />} />
        <MetricCard title="Today's P&L" value={`$${stats?.pnl_today?.toFixed(2) || "0.00"}`} icon={<Activity className="text-zinc-400" size={20} />} valueColor={stats?.pnl_today && stats.pnl_today < 0 ? "text-red-400" : "text-emerald-400"} />
        <MetricCard title="Open Positions" value={stats?.open_positions_count?.toString() || "0"} icon={<Target className="text-zinc-400" size={20} />} />
        <MetricCard title="Safety Guard" value={stats?.safety_guard_status || "UNKNOWN"} icon={<ShieldAlert className="text-zinc-400" size={20} />} valueColor={stats?.safety_guard_status === "OK" ? "text-emerald-400" : "text-amber-400"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 h-96 flex flex-col">
          <h3 className="text-lg font-medium mb-4">Equity Curve (30 days)</h3>
          <div className="flex-1 min-h-0">
            <EquityChart data={equity || []} />
          </div>
        </div>
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 h-96 flex flex-col">
          <h3 className="text-lg font-medium mb-4">Daily P&L (30 days)</h3>
          <div className="flex-1 min-h-0">
            <PnlChart data={pnl || []} />
          </div>
        </div>
      </div>

      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-zinc-800">
          <h3 className="text-lg font-medium">Open Positions</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-zinc-400 uppercase bg-zinc-900/50 border-b border-zinc-800">
              <tr>
                <th className="px-6 py-3">Symbol</th>
                <th className="px-6 py-3">Side</th>
                <th className="px-6 py-3 text-right">Entry</th>
                <th className="px-6 py-3 text-right">Stop</th>
                <th className="px-6 py-3 text-right">Target</th>
                <th className="px-6 py-3 text-right">Qty</th>
                <th className="px-6 py-3 text-right">Expected RRR</th>
                <th className="px-6 py-3 text-right">Opened At</th>
              </tr>
            </thead>
            <tbody>
              {!openTrades?.length ? (
                <tr>
                  <td colSpan={8} className="px-6 py-8 text-center text-zinc-500">No open positions</td>
                </tr>
              ) : (
                openTrades.map((t) => (
                  <tr key={t.id} className="border-b border-zinc-800/50 hover:bg-zinc-800/20">
                    <td className="px-6 py-4 font-medium">{t.symbol}</td>
                    <td className="px-6 py-4">
                      <span className={`px-2 py-1 rounded text-xs ${t.exchange_side === "Buy" ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"}`}>
                        {t.exchange_side.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      {formatMoney(t.entry_price)}
                    </td>
                    <td className="px-6 py-4 text-right text-red-400">
                      {formatMoney(t.stop_price)}
                    </td>
                    <td className="px-6 py-4 text-right text-emerald-400">
                      {formatMoney(t.target_price)}
                    </td>
                    <td className="px-6 py-4 text-right">
                      {formatNumber(t.qty, 6)}
                    </td>
                    <td className="px-6 py-4 text-right">
                      {formatNumber(t.expected_rrr, 2)}
                    </td>
                    <td className="px-6 py-4 text-right text-zinc-400">
                      {t.opened_at ? new Date(t.opened_at).toLocaleString() : "-"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function formatMoney(value: number | null): string {
  if (value === null) {
    return "-";
  }

  return `$${value.toFixed(4)}`;
}

function formatNumber(value: number | null, digits: number): string {
  if (value === null) {
    return "-";
  }

  return value.toFixed(digits);
}

function MetricCard({ title, value, icon, valueColor = "text-zinc-100" }: { title: string; value: string; icon: React.ReactNode; valueColor?: string }) {
  return (
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-5 flex items-start justify-between">
      <div>
        <p className="text-sm font-medium text-zinc-400 mb-1">{title}</p>
        <p className={`text-2xl font-bold ${valueColor}`}>{value}</p>
      </div>
      <div className="p-2 bg-zinc-950 rounded-lg border border-zinc-800">
        {icon}
      </div>
    </div>
  );
}
