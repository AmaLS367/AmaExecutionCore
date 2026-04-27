import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { configApi } from "../api/config";
import { RefreshCw, Settings2, CheckCircle2, AlertCircle } from "lucide-react";

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [reloadStatus, setReloadStatus] = useState<"idle" | "success" | "error">("idle");

  const { data: config, isLoading } = useQuery({
    queryKey: ["config"],
    queryFn: configApi.get,
  });

  const reloadMutation = useMutation({
    mutationFn: configApi.reload,
    onSuccess: () => {
      setReloadStatus("success");
      queryClient.invalidateQueries({ queryKey: ["config"] });
      setTimeout(() => setReloadStatus("idle"), 3000);
    },
    onError: () => {
      setReloadStatus("error");
      setTimeout(() => setReloadStatus("idle"), 3000);
    },
  });

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden flex flex-col relative">
        <div className="px-6 py-5 border-b border-zinc-800 bg-zinc-900/50 flex justify-between items-center">
          <div className="flex items-center gap-3">
            <Settings2 className="text-emerald-500" size={24} />
            <h2 className="text-xl font-bold text-zinc-100">System Configuration</h2>
          </div>
          <button
            onClick={() => reloadMutation.mutate()}
            disabled={reloadMutation.isPending}
            className="flex items-center gap-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 px-4 py-2 rounded-lg transition-colors border border-zinc-700 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RefreshCw size={16} className={reloadMutation.isPending ? "animate-spin" : ""} />
            {reloadMutation.isPending ? "Reloading..." : "Reload Config"}
          </button>
        </div>

        {/* Status Messages */}
        {reloadStatus === "success" && (
          <div className="bg-emerald-500/10 border-b border-emerald-500/20 px-6 py-3 flex items-center gap-2 text-emerald-400 text-sm">
            <CheckCircle2 size={16} />
            Configuration reloaded successfully.
          </div>
        )}
        {reloadStatus === "error" && (
          <div className="bg-red-500/10 border-b border-red-500/20 px-6 py-3 flex items-center gap-2 text-red-400 text-sm">
            <AlertCircle size={16} />
            Failed to reload configuration.
          </div>
        )}

        {/* Config Content */}
        <div className="p-6">
          {isLoading ? (
            <div className="flex justify-center items-center py-12 text-zinc-500">
              Loading configuration...
            </div>
          ) : !config ? (
            <div className="flex justify-center items-center py-12 text-zinc-500">
              Failed to load configuration.
            </div>
          ) : (
            <div className="bg-zinc-950 border border-zinc-800 rounded-lg p-4 overflow-x-auto">
              <pre className="text-sm text-zinc-300 font-mono">
                {JSON.stringify(config, null, 2)}
              </pre>
            </div>
          )}
          <div className="mt-4 text-xs text-zinc-500 flex items-center gap-2">
            <AlertCircle size={14} />
            <p>Sensitive values (like API keys and DB URLs) are automatically redacted by the server.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
