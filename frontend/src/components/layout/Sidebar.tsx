import { NavLink } from "react-router-dom";
import { LayoutDashboard, List, Grid3x3, ScrollText, Settings, LogOut } from "lucide-react";
import { useAuthStore } from "../../store/authStore";
import { authApi } from "../../api/auth";

const links = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/trades", label: "Trades", icon: List },
  { to: "/grid", label: "Grid", icon: Grid3x3 },
  { to: "/logs", label: "Logs", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar() {
  const clearToken = useAuthStore((s) => s.clearToken);

  const handleLogout = async () => {
    await authApi.logout().catch(() => {});
    clearToken();
  };

  return (
    <nav className="w-56 flex flex-col bg-zinc-900 border-r border-zinc-800">
      <div className="px-4 py-5 border-b border-zinc-800">
        <span className="text-sm font-semibold text-brand">AmaExecutionCore</span>
      </div>
      <ul className="flex-1 py-3 space-y-1 px-2">
        {links.map(({ to, label, icon: Icon }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                  isActive
                    ? "bg-brand text-white"
                    : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          </li>
        ))}
      </ul>
      <button
        onClick={handleLogout}
        className="flex items-center gap-3 px-5 py-4 text-sm text-zinc-400 hover:text-zinc-100 border-t border-zinc-800 transition-colors"
      >
        <LogOut size={16} />
        Logout
      </button>
    </nav>
  );
}
