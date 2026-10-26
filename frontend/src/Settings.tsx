import { FormEvent, useEffect, useState } from "react";
import { ApiError, api, Prefs } from "./api";
import { permission, requestPermission, Permission } from "./browserNotify";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Settings({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [timezone, setTimezone] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [perm, setPerm] = useState<Permission>(permission());

  useEffect(() => {
    api
      .getPrefs()
      .then((p) => {
        const { timezone: tz, last_reminder_at: _ignored, ...rest } = p;
        setPrefs(rest);
        setTimezone(tz);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) onUnauthorized();
        else setMessage({ ok: false, text: "Could not load your settings." });
      });
  }, [onUnauthorized]);

  if (!prefs) return <p className="text-slate-500">{message?.text ?? "Loading..."}</p>;

  const set = (patch: Partial<Prefs>) => setPrefs({ ...prefs, ...patch });
  const toggleDay = (d: number) =>
    set({ work_days: prefs.work_days.includes(d) ? prefs.work_days.filter((x) => x !== d) : [...prefs.work_days, d].sort() });

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await api.putPrefs(prefs!);
      setMessage({ ok: true, text: "Saved." });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauthorized();
      else setMessage({ ok: false, text: err instanceof ApiError ? err.message : "Could not reach the server." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="max-w-xl space-y-6">
      <section>
        <h2 className="text-lg font-semibold text-slate-900">Reminders</h2>
        <p className="text-sm text-slate-600">
          TaskPilot lists your pending tasks (P1 first, with time left) on a schedule, only during your working hours. Times are in {timezone}.
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm font-medium text-slate-800">
          <input type="checkbox" checked={prefs.reminders_enabled} onChange={(e) => set({ reminders_enabled: e.target.checked })} />
          Send me reminders
        </label>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-sm text-slate-700">
            Remind me every (minutes)
            <input
              type="number"
              min={1}
              max={1440}
              value={prefs.interval_minutes}
              onChange={(e) => set({ interval_minutes: Number(e.target.value) })}
              className="mt-1 block w-32 rounded-md border border-slate-300 px-2 py-1"
            />
          </label>
          <button type="button" onClick={() => set({ interval_minutes: 60 })} className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">
            60 min
          </button>
          <button type="button" onClick={() => set({ interval_minutes: 1 })} className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50">
            Demo: 1 min
          </button>
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Working hours</h2>
        <p className="text-sm text-slate-600">No reminders outside these hours or days. The end time is exclusive; overnight shifts are not supported.</p>
        <div className="mt-3 flex gap-3">
          <label className="text-sm text-slate-700">
            From
            <input type="time" value={prefs.work_start} onChange={(e) => set({ work_start: e.target.value })} className="mt-1 block rounded-md border border-slate-300 px-2 py-1" />
          </label>
          <label className="text-sm text-slate-700">
            To
            <input type="time" value={prefs.work_end} onChange={(e) => set({ work_end: e.target.value })} className="mt-1 block rounded-md border border-slate-300 px-2 py-1" />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {DAYS.map((name, d) => (
            <label
              key={name}
              className={`cursor-pointer rounded-full border px-3 py-1 text-sm ${
                prefs.work_days.includes(d) ? "border-indigo-600 bg-indigo-50 text-indigo-800" : "border-slate-300 text-slate-600"
              }`}
            >
              <input type="checkbox" className="sr-only" checked={prefs.work_days.includes(d)} onChange={() => toggleDay(d)} />
              {name}
            </label>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Browser notifications</h2>
        {perm === "unsupported" && <p className="text-sm text-slate-600">This browser does not support notifications. You will still see them in the bell.</p>}
        {perm === "granted" && <p className="text-sm text-green-700">Enabled. New reminders and missed deadlines will pop up.</p>}
        {perm === "denied" && <p className="text-sm text-amber-800">Blocked for this site. Allow notifications in the browser's site settings to turn them on.</p>}
        {perm === "default" && (
          <button type="button" onClick={async () => setPerm(await requestPermission())} className="mt-2 rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
            Enable browser notifications
          </button>
        )}
      </section>

      <div className="flex items-center gap-3">
        <button disabled={busy} className="rounded-md bg-indigo-600 px-5 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
          {busy ? "Saving..." : "Save settings"}
        </button>
        {message && (
          <span role="status" className={`text-sm ${message.ok ? "text-green-700" : "text-red-700"}`}>
            {message.text}
          </span>
        )}
      </div>
    </form>
  );
}
