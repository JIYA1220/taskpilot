import { FormEvent, useState } from "react";
import { ApiError, api, DependencyItem, DirectoryTeam } from "./api";
import { toLocalInput } from "./time";

type Common = {
  teams: DirectoryTeam[];
  ownTeam: string;
  onChanged: () => void;
  onUnauthorized: () => void;
};

function useAction(onChanged: () => void, onUnauthorized: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      else setError(e instanceof ApiError ? e.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, run };
}

const METHOD_NOTE: Record<string, string> = {
  keyword: "matched from what each team owns",
  llm: "chosen by the AI from the likely teams",
  step_a: "the AI's first guess",
  manual: "chosen by you",
  none: "not identified",
};

function OpenDependency({ dep, teams, ownTeam, onChanged, onUnauthorized }: Common & { dep: DependencyItem }) {
  const { busy, error, run } = useAction(onChanged, onUnauthorized);
  const [what, setWhat] = useState(dep.what_is_needed);
  const [team, setTeam] = useState(dep.depends_on_team_id ?? "");
  const [by, setBy] = useState(toLocalInput(dep.needed_by));
  const [asked, setAsked] = useState(dep.already_asked);
  const waiting = dep.direction === "need_from_them";
  const input = "mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm";

  const save = (e: FormEvent) => {
    e.preventDefault();
    return run(() =>
      api.updateDependency(dep.id, {
        what_is_needed: what.trim() || dep.what_is_needed,
        needed_by: by ? new Date(by).toISOString() : null,
        already_asked: asked.trim(),
        team_id: team || null,
      }),
    );
  };

  return (
    <form onSubmit={save} className="mt-2 rounded-md border border-red-200 bg-red-50/60 p-3">
      <p className="text-sm font-medium text-red-800">
        <span className="mr-2 rounded bg-red-600 px-1.5 py-0.5 text-xs font-semibold text-white">{waiting ? "Blocked" : "Waiting on you"}</span>
        {waiting ? `Waiting on ${dep.team_name}` : `${dep.team_name} is waiting on you`}
      </p>
      <p className="text-xs text-slate-600">
        Team {dep.match_method === "none" ? "not identified yet" : METHOD_NOTE[dep.match_method] ?? dep.match_method}. A few quick questions:
      </p>
      <div className="mt-2 grid gap-2 sm:grid-cols-2">
        <label className="text-xs text-slate-600 sm:col-span-2">
          What exactly is needed?
          <input value={what} maxLength={300} onChange={(e) => setWhat(e.target.value)} className={input} />
        </label>
        <label className="text-xs text-slate-600">
          Which team?
          <select value={team} onChange={(e) => setTeam(e.target.value)} className={input}>
            <option value="">Not identified yet</option>
            {teams.filter((t) => t.id !== ownTeam).map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          By when do you need it?
          <input type="datetime-local" value={by} onChange={(e) => setBy(e.target.value)} className={input} />
        </label>
        <label className="text-xs text-slate-600 sm:col-span-2">
          Who have you already asked?
          <input value={asked} maxLength={300} onChange={(e) => setAsked(e.target.value)} placeholder="e.g. Meera, on Slack yesterday" className={input} />
        </label>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
      <div className="mt-2 flex gap-2">
        <button disabled={busy} className="rounded-md border border-slate-300 px-3 py-1 text-xs hover:bg-white disabled:opacity-50">Save answers</button>
        <button
          type="button"
          disabled={busy}
          onClick={() => run(() => api.resolveDependency(dep.id))}
          className="rounded-md bg-green-700 px-3 py-1 text-xs font-medium text-white hover:bg-green-800 disabled:opacity-50"
        >
          {waiting ? "Mark resolved (unblocks the task)" : "Mark resolved"}
        </button>
      </div>
    </form>
  );
}

function AddDependency({ taskId, teams, ownTeam, onChanged, onUnauthorized }: Common & { taskId: number }) {
  const { busy, error, run } = useAction(onChanged, onUnauthorized);
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState("need_from_them");
  const [what, setWhat] = useState("");
  const [team, setTeam] = useState("");
  const input = "mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm";

  if (!open)
    return (
      <button onClick={() => setOpen(true)} className="mt-2 text-xs text-slate-500 underline hover:text-slate-800">
        Flag a dependency the AI missed
      </button>
    );
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        run(async () => {
          await api.addDependency(taskId, { direction, what_is_needed: what.trim(), team_id: team || null });
          setOpen(false);
          setWhat("");
        });
      }}
      className="mt-2 rounded-md border border-slate-200 p-3"
    >
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-xs text-slate-600">
          Which way?
          <select value={direction} onChange={(e) => setDirection(e.target.value)} className={input}>
            <option value="need_from_them">I need something from another team</option>
            <option value="they_need_from_me">Another team needs something from me</option>
          </select>
        </label>
        <label className="text-xs text-slate-600">
          Team
          <select value={team} onChange={(e) => setTeam(e.target.value)} className={input}>
            <option value="">Work it out for me</option>
            {teams.filter((t) => t.id !== ownTeam).map((t) => (
              <option key={t.id} value={t.id}>{t.name}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600 sm:col-span-2">
          What is needed?
          <input value={what} maxLength={300} onChange={(e) => setWhat(e.target.value)} placeholder="e.g. access to the staging environment" className={input} />
        </label>
      </div>
      {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
      <div className="mt-2 flex gap-2">
        <button disabled={busy || !what.trim()} className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50">Add</button>
        <button type="button" onClick={() => setOpen(false)} className="rounded-md border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50">Cancel</button>
      </div>
    </form>
  );
}

/** Everything about a task's dependencies: the red flag, the follow-up questions, and a way to add one. */
export default function DependencyPanel({ taskId, taskOpen, deps, ...common }: Common & { taskId: number; taskOpen: boolean; deps: DependencyItem[] }) {
  const open = deps.filter((d) => d.state === "open");
  const resolved = deps.filter((d) => d.state === "resolved");
  return (
    <div>
      {open.map((d) => (
        <OpenDependency key={`${d.id}-${d.what_is_needed}-${d.depends_on_team_id}`} dep={d} {...common} />
      ))}
      {resolved.map((d) => (
        <p key={d.id} className="mt-1 text-xs text-green-800">
          Resolved: {d.team_name} {d.direction === "need_from_them" ? "delivered" : "received"} {d.what_is_needed || "what was needed"}.
        </p>
      ))}
      {taskOpen && <AddDependency taskId={taskId} {...common} />}
    </div>
  );
}
