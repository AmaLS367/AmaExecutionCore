import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { gridApi } from "../api/grid";
import { ArrowRight, Activity, X } from "lucide-react";

export function GridPage() {
  const [selectedSessionId, setSelectedSessionId] = useState<number | null>(null);

  const { data: sessions, isLoading: sessionsLoading } = useQuery({
    queryKey: ["grid-sessions"],
    queryFn: gridApi.getSessions,
  });

  const { data: sessionDetail, isLoading: detailLoading } = useQuery({
    queryKey: ["grid-session-detail", selectedSessionId],
    queryFn: () => gridApi.getSession(selectedSessionId!),
    enabled: selectedSessionId !== null,
  });

  return (
    <div className="flex h-full gap-6">
      {/* Sidebar with Sessions */}
      <div className={`flex flex-col bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden transition-all ${selectedSessionId ? "w-1/3" : "w-full"}`}>
        <div className="px-5 py-4 border-b border-zinc-800 flex justify-between items-center bg-zinc-900/50">
          <h3 className="text-lg font-medium">Grid Sessions</h3>
          <span className="text-xs bg-emerald-500/10 text-emerald-400 px-2 py-1 rounded">
            {sessions?.length || 0} Total
          </span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {sessionsLoading ? (
            <div className="p-6 text-center text-zinc-500">Loading sessions...</div>
          ) : !sessions?.length ? (
            <div className="p-6 text-center text-zinc-500">No grid sessions found.</div>
          ) : (
            <ul className="divide-y divide-zinc-800/50">
              {sessions.map((session) => (
                <li key={session.id}>
                  <button
                    onClick={() => setSelectedSessionId(session.id)}
                    className={`w-full text-left p-5 transition-colors hover:bg-zinc-800/30 flex items-center justify-between ${
                      selectedSessionId === session.id ? "bg-zinc-800/50 border-l-2 border-emerald-500" : "border-l-2 border-transparent"
                    }`}
                  >
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-semibold text-zinc-100">{session.symbol}</span>
                        <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${
                          session.status === "active" ? "bg-emerald-500/10 text-emerald-400" :
                          session.status === "paused" ? "bg-amber-500/10 text-amber-400" :
                          session.status === "waiting_reentry" ? "bg-blue-500/10 text-blue-400" :
                          "bg-zinc-800 text-zinc-400"
                        }`}>
                          {session.status}
                        </span>
                      </div>
                      <div className="text-xs text-zinc-500">
                        ID: {session.id} • Created: {session.created_at ? new Date(session.created_at).toLocaleDateString() : "Unknown"}
                      </div>
                    </div>
                    <ArrowRight className="text-zinc-600" size={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Details View */}
      {selectedSessionId && (
        <div className="flex-1 bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden flex flex-col relative">
          {detailLoading ? (
            <div className="flex-1 flex flex-col items-center justify-center text-zinc-500 space-y-4">
              <Activity className="animate-spin text-emerald-500" size={32} />
              <p>Loading session details...</p>
            </div>
          ) : sessionDetail ? (
            <>
              <div className="px-6 py-5 border-b border-zinc-800 bg-zinc-900/50 flex justify-between items-start">
                <div>
                  <h2 className="text-xl font-bold text-zinc-100 mb-2">Session #{sessionDetail.id}</h2>
                  <div className="flex flex-wrap gap-4 text-sm">
                    <div><span className="text-zinc-500">Symbol:</span> <span className="font-medium text-zinc-200">{sessionDetail.symbol}</span></div>
                    <div><span className="text-zinc-500">Status:</span> <span className="font-medium text-zinc-200">{sessionDetail.status}</span></div>
                    <div><span className="text-zinc-500">Total Slots:</span> <span className="font-medium text-zinc-200">{sessionDetail.slots.length}</span></div>
                    <div>
                      <span className="text-zinc-500">Total P&L:</span> 
                      <span className={`font-medium ml-1 ${
                        sessionDetail.slots.reduce((acc, s) => acc + s.realized_pnl, 0) >= 0 ? "text-emerald-400" : "text-red-400"
                      }`}>
                        ${sessionDetail.slots.reduce((acc, s) => acc + s.realized_pnl, 0).toFixed(2)}
                      </span>
                    </div>
                  </div>
                </div>
                <button 
                  onClick={() => setSelectedSessionId(null)}
                  className="p-2 hover:bg-zinc-800 rounded text-zinc-400 hover:text-zinc-100 transition-colors"
                >
                  <X size={20} />
                </button>
              </div>
              <div className="flex-1 overflow-auto p-6 space-y-6">
                
                {/* Config View */}
                <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-4">
                  <h4 className="text-xs font-semibold uppercase text-zinc-500 mb-3 tracking-wider">Configuration</h4>
                  <pre className="text-xs text-emerald-400/80 font-mono overflow-x-auto">
                    {JSON.stringify(sessionDetail.config, null, 2)}
                  </pre>
                </div>

                {/* Slots Table */}
                <div>
                  <h4 className="text-xs font-semibold uppercase text-zinc-500 mb-3 tracking-wider">Grid Slots</h4>
                  <div className="border border-zinc-800 rounded-lg overflow-hidden">
                    <table className="w-full text-sm text-left">
                      <thead className="text-xs text-zinc-400 uppercase bg-zinc-900">
                        <tr>
                          <th className="px-4 py-3">Level</th>
                          <th className="px-4 py-3">Buy Price</th>
                          <th className="px-4 py-3">Sell Price</th>
                          <th className="px-4 py-3">Status</th>
                          <th className="px-4 py-3 text-right">Cycles</th>
                          <th className="px-4 py-3 text-right">P&L</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-800">
                        {sessionDetail.slots.map((slot) => (
                          <tr key={slot.id} className="hover:bg-zinc-800/30 bg-zinc-950/50">
                            <td className="px-4 py-3 font-medium text-zinc-300">Level {slot.level}</td>
                            <td className="px-4 py-3 text-emerald-400">${slot.buy_price.toFixed(4)}</td>
                            <td className="px-4 py-3 text-red-400">${slot.sell_price.toFixed(4)}</td>
                            <td className="px-4 py-3">
                              <span className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                                slot.status === "waiting_buy" ? "bg-emerald-500/10 text-emerald-400" :
                                slot.status === "waiting_sell" ? "bg-red-500/10 text-red-400" :
                                "bg-zinc-800 text-zinc-400"
                              }`}>
                                {slot.status}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-right">{slot.completed_cycles}</td>
                            <td className={`px-4 py-3 text-right ${slot.realized_pnl > 0 ? "text-emerald-400" : slot.realized_pnl < 0 ? "text-red-400" : "text-zinc-500"}`}>
                              ${slot.realized_pnl.toFixed(2)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-500">Failed to load details.</div>
          )}
        </div>
      )}
    </div>
  );
}
