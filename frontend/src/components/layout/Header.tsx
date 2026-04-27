import { useAuthStore } from "../../store/authStore";
import { UserCircle } from "lucide-react";

export function Header() {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  return (
    <header className="h-16 flex items-center justify-between px-6 bg-zinc-900 border-b border-zinc-800">
      <div className="flex items-center gap-2">
        <h2 className="text-lg font-semibold text-zinc-100 tracking-tight">Admin Dashboard</h2>
      </div>
      <div className="flex items-center gap-4">
        {isAuthenticated && (
          <div className="flex items-center gap-2 text-sm text-zinc-400 bg-zinc-950 px-3 py-1.5 rounded-full border border-zinc-800">
            <UserCircle size={16} className="text-emerald-500" />
            <span>Admin</span>
          </div>
        )}
      </div>
    </header>
  );
}
