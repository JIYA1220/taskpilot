import { useCallback, useEffect, useState } from "react";
import { api, getToken, Me, setToken } from "./api";
import Bell from "./Bell";
import Login from "./Login";
import Settings from "./Settings";
import Today from "./Today";

type Tab = "today" | "settings";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(() => getToken() !== null);
  const [tab, setTab] = useState<Tab>("today");
  const [reload, setReload] = useState(0);
  const refreshTasks = useCallback(() => setReload((n) => n + 1), []);

  const load = useCallback(() => {
    setChecking(true);
    api
      .me()
      .then(setMe)
      .catch(() => {
        setToken(null);
        setMe(null);
      })
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (getToken()) load();
  }, [load]);

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
  }, []);

  if (checking) return <p className="p-8 text-slate-500">Loading...</p>;
  if (!me) return <Login onLoggedIn={load} />;

  return (
    <main className="mx-auto max-w-3xl p-6 font-sans text-slate-800">
      <header className="mb-6 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
        <div className="flex items-baseline gap-6">
          <h1 className="text-2xl font-semibold text-slate-900">TaskPilot</h1>
          <nav className="flex gap-4 text-sm">
            {(["today", "settings"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={tab === t ? "border-b-2 border-indigo-600 font-semibold text-indigo-800" : "text-slate-600 hover:text-slate-900"}
              >
                {t === "today" ? "Today" : "Settings"}
              </button>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-4 text-sm text-slate-600">
          <Bell onUnauthorized={logout} onNew={refreshTasks} />
          <span>
            {me.name} ({me.team_id})
          </span>
          <button onClick={logout} className="text-indigo-700 hover:underline">
            Sign out
          </button>
        </div>
      </header>
      {tab === "today" ? <Today onUnauthorized={logout} reloadSignal={reload} /> : <Settings onUnauthorized={logout} />}
    </main>
  );
}
