import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import OtpInput from "react-otp-input";
import { Lock, User, ShieldCheck } from "lucide-react";
import { authApi } from "../api/auth";
import { useAuthStore } from "../store/authStore";

export function LoginPage() {
  const [step, setStep] = useState<1 | 2>(1);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [sessionToken, setSessionToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const setToken = useAuthStore((state) => state.setToken);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await authApi.login(username, password);
      if (res.totp_required) {
        setSessionToken(res.session_token);
        setStep(2);
      } else {
        // Fallback
        setToken(res.session_token);
        navigate("/");
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || "Invalid credentials");
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyTotp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (totpCode.length !== 6) return;
    setError(null);
    setLoading(true);
    try {
      const res = await authApi.verifyTotp(sessionToken, totpCode);
      setToken(res.access_token);
      navigate("/");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Invalid TOTP code");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-950 text-zinc-50 p-4">
      <div className="w-full max-w-md bg-zinc-900 border border-zinc-800 rounded-xl shadow-2xl p-8">
        <div className="text-center mb-8">
          <div className="mx-auto w-12 h-12 bg-emerald-500/10 text-emerald-500 rounded-full flex items-center justify-center mb-4">
            <ShieldCheck className="w-6 h-6" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">AmaExecutionCore</h1>
          <p className="text-zinc-400 mt-2 text-sm">Secure Admin Panel</p>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 text-sm p-3 rounded-lg mb-6">
            {error}
          </div>
        )}

        {step === 1 ? (
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-zinc-400 mb-1">
                Username
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <User className="h-5 w-5 text-zinc-500" />
                </div>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="block w-full pl-10 pr-3 py-2 border border-zinc-800 rounded-lg bg-zinc-950 text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition-colors"
                  placeholder="admin"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-zinc-400 mb-1">
                Password
              </label>
              <div className="relative">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Lock className="h-5 w-5 text-zinc-500" />
                </div>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="block w-full pl-10 pr-3 py-2 border border-zinc-800 rounded-lg bg-zinc-950 text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition-colors"
                  placeholder="••••••••"
                  required
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full flex justify-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-emerald-500 focus:ring-offset-zinc-900 disabled:opacity-50 disabled:cursor-not-allowed transition-colors mt-6"
            >
              {loading ? "Authenticating..." : "Sign In"}
            </button>
          </form>
        ) : (
          <form onSubmit={handleVerifyTotp} className="space-y-6">
            <div className="text-center">
              <label className="block text-sm font-medium text-zinc-400 mb-4">
                Enter 6-digit TOTP code
              </label>
              <div className="flex justify-center">
                <OtpInput
                  value={totpCode}
                  onChange={setTotpCode}
                  numInputs={6}
                  renderSeparator={<span className="mx-1 text-zinc-600">-</span>}
                  renderInput={(props) => (
                    <input
                      {...props}
                      style={{ width: "2.5rem", height: "3rem" }}
                      className="text-center text-lg font-mono border border-zinc-800 rounded-lg bg-zinc-950 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:border-transparent transition-colors"
                    />
                  )}
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading || totpCode.length !== 6}
              className="w-full flex justify-center py-2.5 px-4 border border-transparent rounded-lg shadow-sm text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-emerald-500 focus:ring-offset-zinc-900 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "Verifying..." : "Verify Code"}
            </button>
            <button
              type="button"
              onClick={() => {
                setStep(1);
                setTotpCode("");
                setSessionToken("");
              }}
              className="w-full text-sm text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              Back to login
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
