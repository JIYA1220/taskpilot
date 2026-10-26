import { Conflict, ConflictSuggestion } from "./api";
import { formatDeadline } from "./time";

type Props = {
  conflicts: Conflict[];
  dismissed: Set<string>;
  busy: boolean;
  onDismiss: (key: string) => void;
  onMove: (s: ConflictSuggestion) => void;
};

export const conflictKey = (c: Conflict) => `${c.kind}:${c.task_ids.join(",")}:${c.minutes_needed ?? ""}`;

/** Schedule conflicts: shown in red, with suggestions the person accepts or ignores. Nothing moves on its own. */
export default function Conflicts({ conflicts, dismissed, busy, onDismiss, onMove }: Props) {
  const visible = conflicts.filter((c) => !dismissed.has(conflictKey(c)));
  if (visible.length === 0) return null;
  return (
    <section aria-label="Schedule conflicts" className="space-y-3">
      {visible.map((c) => (
        <div key={conflictKey(c)} className="rounded-lg border border-red-300 bg-red-50 p-3">
          <p className="text-sm font-semibold text-red-800">
            <span className="mr-2 rounded bg-red-600 px-1.5 py-0.5 text-xs text-white">Schedule conflict</span>
            {c.kind === "overload" ? "Too much for today" : "Two P1 tasks, same deadline"}
          </p>
          <p className="mt-1 text-sm text-slate-800">{c.message}</p>
          {c.suggestions.length > 0 && (
            <div className="mt-2">
              <p className="text-xs font-medium text-slate-700">Suggestion: move {c.suggestions.length === 1 ? "this task" : "these tasks"} to the next working day.</p>
              <ul className="mt-1 space-y-1">
                {c.suggestions.map((s) => (
                  <li key={s.task_id} className="flex flex-wrap items-center gap-2 text-sm text-slate-800">
                    <span className="rounded bg-slate-200 px-1.5 text-xs font-semibold">{s.priority}</span>
                    {s.title} ({s.estimated_minutes} min)
                    <button
                      disabled={busy}
                      onClick={() => onMove(s)}
                      className="rounded border border-red-400 px-2 py-0.5 text-xs text-red-800 hover:bg-red-100 disabled:opacity-50"
                    >
                      Move to {formatDeadline(s.proposed_deadline)}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <button onClick={() => onDismiss(conflictKey(c))} className="mt-2 text-xs text-slate-600 underline hover:text-slate-900">
            Keep as it is
          </button>
        </div>
      ))}
    </section>
  );
}
