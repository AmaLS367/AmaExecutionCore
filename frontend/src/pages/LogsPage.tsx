import { useState, useRef, useEffect } from "react";
import { useLiveLogs } from "../hooks/useLiveLogs";
import { Play, Pause, Trash2, Filter } from "lucide-react";

export function LogsPage() {
  const { logs, isConnected, clearLogs } = useLiveLogs();
  const [autoScroll, setAutoScroll] = useState(true);
  const [levelFilter, setLevelFilter] = useState<string>("ALL");
  const [moduleFilter, setModuleFilter] = useState("");
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs, autoScroll]);

  const filteredLogs = logs.filter((log) => {
    if (levelFilter !== "ALL" && log.level !== levelFilter) return false;
    if (moduleFilter && !log.module.toLowerCase().includes(moduleFilter.toLowerCase())) return false;
    return true;
  });

  const getLevelColor = (level: string) => {
    switch (level) {
      case "INFO": return "text-blue-400";
      case "WARNING": return "text-amber-400";
      case "ERROR": return "text-red-400";
      case "DEBUG": return "text-zinc-500";
      default: return "text-zinc-300";
    }
  };

  return (
    <div className="h-full flex flex-col bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
      {/* Header & Controls */}
      <div className="px-5 py-4 border-b border-zinc-800 bg-zinc-900/50 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <h3 className="text-lg font-medium flex items-center gap-2">
            Live Logs
            <span className="relative flex h-3 w-3">
              <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${isConnected ? "bg-emerald-400" : "bg-red-400"}`}></span>
              <span className={`relative inline-flex rounded-full h-3 w-3 ${isConnected ? "bg-emerald-500" : "bg-red-500"}`}></span>
            </span>
          </h3>
          <span className="text-xs text-zinc-500">{isConnected ? "Connected" : "Disconnected"}</span>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-zinc-950 px-2 py-1.5 rounded border border-zinc-800">
            <Filter size={14} className="text-zinc-500" />
            <select
              value={levelFilter}
              onChange={(e) => setLevelFilter(e.target.value)}
              className="bg-transparent text-sm text-zinc-300 focus:outline-none"
            >
              <option value="ALL">All Levels</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
              <option value="DEBUG">DEBUG</option>
            </select>
          </div>

          <input
            type="text"
            placeholder="Filter module..."
            value={moduleFilter}
            onChange={(e) => setModuleFilter(e.target.value)}
            className="bg-zinc-950 border border-zinc-800 rounded px-3 py-1.5 text-sm text-zinc-100 focus:outline-none focus:border-emerald-500"
          />

          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={`flex items-center gap-1 px-3 py-1.5 rounded text-sm transition-colors border ${
              autoScroll ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" : "bg-zinc-800 text-zinc-400 border-zinc-700"
            }`}
          >
            {autoScroll ? <Pause size={14} /> : <Play size={14} />}
            Auto-scroll
          </button>

          <button
            onClick={clearLogs}
            className="flex items-center gap-1 px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded text-sm transition-colors border border-zinc-700"
          >
            <Trash2 size={14} />
            Clear
          </button>
        </div>
      </div>

      {/* Log Output */}
      <div className="flex-1 overflow-y-auto p-4 bg-[#0d0d0f] font-mono text-xs">
        {filteredLogs.length === 0 ? (
          <div className="h-full flex items-center justify-center text-zinc-600">
            {logs.length === 0 ? "Waiting for logs..." : "No logs match the current filters."}
          </div>
        ) : (
          <div className="space-y-1">
            {filteredLogs.map((log, i) => (
              <div key={i} className="hover:bg-zinc-800/30 flex gap-4 break-all">
                <span className="text-zinc-600 shrink-0">{new Date(log.timestamp).toISOString()}</span>
                <span className={`shrink-0 w-16 font-semibold ${getLevelColor(log.level)}`}>{log.level}</span>
                <span className="text-purple-400 shrink-0 w-48 truncate" title={log.module}>[{log.module}]</span>
                <span className="text-zinc-300">{log.message}</span>
              </div>
            ))}
            <div ref={logsEndRef} />
          </div>
        )}
      </div>
    </div>
  );
}
