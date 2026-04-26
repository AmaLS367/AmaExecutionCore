import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell } from "recharts";
import type { DailyPnlPoint } from "../../types/stats";

export function PnlChart({ data }: { data: DailyPnlPoint[] }) {
  if (!data?.length) return <div className="h-full flex items-center justify-center text-zinc-500">No data available</div>;

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
        <XAxis dataKey="date" stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} />
        <YAxis stroke="#71717a" fontSize={12} tickLine={false} axisLine={false} tickFormatter={(val) => `$${val}`} />
        <Tooltip
          contentStyle={{ backgroundColor: "#18181b", border: "1px solid #27272a", borderRadius: "8px" }}
          cursor={{ fill: "#27272a" }}
        />
        <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
          {data.map((entry, index) => (
            <Cell key={`cell-${index}`} fill={entry.pnl >= 0 ? "#10b981" : "#ef4444"} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
