import { useEffect, useState } from "react";

type Health = { status: string; email_backend: string; groq_key_configured: boolean };

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setHealth)
      .catch(() => setError(true));
  }, []);

  return (
    <main className="mx-auto max-w-xl p-8 font-sans text-slate-800">
      <h1 className="text-3xl font-semibold">TaskPilot</h1>
      <p className="mt-2 text-slate-600">AI work tracker for small teams. Milestone 0: scaffolding.</p>
      <section className="mt-6 rounded-lg border border-slate-200 p-4">
        <h2 className="font-medium">Backend</h2>
        {error && <p className="mt-2 text-red-600">Cannot reach the API on /api/health.</p>}
        {!error && !health && <p className="mt-2 text-slate-500">Checking...</p>}
        {health && (
          <ul className="mt-2 text-sm text-slate-700">
            <li>Status: {health.status}</li>
            <li>Email backend: {health.email_backend}</li>
            <li>Groq key configured: {health.groq_key_configured ? "yes" : "no"}</li>
          </ul>
        )}
      </section>
    </main>
  );
}
