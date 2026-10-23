import { useCallback, useEffect, useState } from "react";
import { api, getToken, Me, setToken } from "./api";
import Login from "./Login";
import Today from "./Today";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(() => getToken() !== null);

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
      <header className="mb-6 flex items-baseline justify-between border-b border-slate-200 pb-3">
        <h1 className="text-2xl font-semibold text-slate-900">TaskPilot</h1>
        <div className="text-sm text-slate-600">
          {me.name} ({me.team_id}){" "}
          <button onClick={logout} className="ml-2 text-indigo-700 hover:underline">
            Sign out
          </button>
        </div>
      </header>
      <Today onUnauthorized={logout} />
    </main>
  );
}
