import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Outcome, Proposed, Question, SavedTask } from "./api";

const PRIORITY_STYLE: Record<string, string> = {
  P1: "bg-red-100 text-red-800",
  P2: "bg-amber-100 text-amber-800",
  P3: "bg-slate-200 text-slate-700",
};

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDeadline(iso: string | null): string {
  if (!iso) return "No deadline";
  return new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

function Badge({ priority }: { priority: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-semibold ${PRIORITY_STYLE[priority] ?? ""}`}>{priority}</span>;
}

function dependencyLabel(t: { dependency_hint: { direction: string; team: string; what_is_needed: string } | null }) {
  const d = t.dependency_hint;
  if (!d || d.direction === "none") return null;
  const who = d.team === "unknown" ? "another team" : d.team;
  return d.direction === "need_from_them" ? `Needs from ${who}: ${d.what_is_needed}` : `${who} needs from you: ${d.what_is_needed}`;
}

export default function Today({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Proposed[] | null>(null);
  const [saved, setSaved] = useState<SavedTask[]>([]);

  const guard = useCallback(
    async <T,>(work: () => Promise<T>): Promise<T | undefined> => {
      setBusy(true);
      setError("");
      try {
        return await work();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) onUnauthorized();
        else setError(e instanceof ApiError ? e.message : "Could not reach the server.");
      } finally {
        setBusy(false);
      }
    },
    [onUnauthorized],
  );

  const refresh = useCallback(() => guard(() => api.listTasks()).then((t) => t && setSaved(t)), [guard]);
  useEffect(() => {
    refresh();
  }, [refresh]);

  function receive(result: Outcome) {
    setOutcome(result);
    setAnswers({});
    if (result.status === "ready") setDraft(result.tasks);
  }

  async function organize() {
    const result = await guard(() => api.organize(text));
    if (result) receive(result);
  }

  async function submitAnswers(skipAll: boolean) {
    if (!outcome) return;
    const payload: Record<string, string | null> = {};
    for (const q of outcome.questions) payload[q.id] = skipAll ? null : answers[q.id]?.trim() || null;
    const result = await guard(() => api.answer(outcome.thread_id, payload));
    if (result) receive(result);
  }

  async function save() {
    if (!draft) return;
    const ok = await guard(() => api.saveTasks(draft));
    if (ok) {
      setDraft(null);
      setOutcome(null);
      setText("");
      refresh();
    }
  }

  function discard() {
    setDraft(null);
    setOutcome(null);
  }

  const update = (i: number, patch: Partial<Proposed>) =>
    setDraft((d) => d && d.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  const asking = outcome?.status === "needs_answers";

  return (
    <div className="space-y-8">
      {error && <p role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}

      {!asking && !draft && (
        <section>
          <h2 className="text-lg font-semibold text-slate-900">What is on your plate today?</h2>
          <p className="text-sm text-slate-600">Write tasks in plain language, one per line or all in a paragraph.</p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            maxLength={4000}
            placeholder={"ask payments team for the refund API\nsend weekly report to Ravi by 5"}
            className="mt-3 w-full rounded-md border border-slate-300 p-3"
          />
          <button
            onClick={organize}
            disabled={busy || !text.trim()}
            className="mt-2 rounded-md bg-indigo-600 px-5 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {busy ? "Organizing..." : "Organize"}
          </button>
        </section>
      )}

      {asking && outcome && (
        <section>
          <h2 className="text-lg font-semibold text-slate-900">A few quick questions</h2>
          <p className="text-sm text-slate-600">
            Found {outcome.extracted_titles.length} task(s): {outcome.extracted_titles.join(" / ")}. Tap a suggestion or type your
            own. You can skip any of them.
          </p>
          <div className="mt-4 space-y-4">
            {outcome.questions.map((q: Question) => (
              <div key={q.id} className="rounded-lg border border-slate-200 p-4">
                <p className="text-xs uppercase tracking-wide text-slate-500">{q.task_title}</p>
                <p className="mt-1 font-medium text-slate-900">{q.question}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {q.suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setAnswers((a) => ({ ...a, [q.id]: s }))}
                      className={`rounded-full border px-3 py-1 text-sm ${
                        answers[q.id] === s ? "border-indigo-600 bg-indigo-50 text-indigo-800" : "border-slate-300 hover:bg-slate-50"
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
                <input
                  value={answers[q.id] ?? ""}
                  onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
                  placeholder="Or type your own answer"
                  maxLength={300}
                  className="mt-2 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm"
                />
              </div>
            ))}
          </div>
          <div className="mt-4 flex gap-2">
            <button
              onClick={() => submitAnswers(false)}
              disabled={busy}
              className="rounded-md bg-indigo-600 px-5 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy ? "Working..." : "Continue"}
            </button>
            <button
              onClick={() => submitAnswers(true)}
              disabled={busy}
              className="rounded-md border border-slate-300 px-5 py-2 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Skip all
            </button>
          </div>
        </section>
      )}

      {draft && (
        <section>
          <h2 className="text-lg font-semibold text-slate-900">Review your tasks</h2>
          <p className="text-sm text-slate-600">Everything is editable. Nothing is saved until you press Save.</p>
          {outcome?.warnings.map((w) => (
            <p key={w} className="mt-2 rounded-md bg-amber-50 p-3 text-sm text-amber-800">{w}</p>
          ))}
          <div className="mt-4 space-y-4">
            {draft.map((t, i) => (
              <div key={i} className="rounded-lg border border-slate-200 p-4">
                <div className="flex items-center gap-2">
                  <input
                    value={t.title}
                    onChange={(e) => update(i, { title: e.target.value })}
                    className="flex-1 rounded-md border border-slate-300 px-3 py-1.5 font-medium"
                    aria-label="Title"
                  />
                  <select
                    value={t.priority}
                    onChange={(e) => update(i, { priority: e.target.value as Proposed["priority"] })}
                    className="rounded-md border border-slate-300 px-2 py-1.5"
                    aria-label="Priority"
                  >
                    <option>P1</option>
                    <option>P2</option>
                    <option>P3</option>
                  </select>
                  <button
                    onClick={() => setDraft((d) => d && d.filter((_, j) => j !== i))}
                    className="text-sm text-slate-500 hover:text-red-600"
                    aria-label="Remove task"
                  >
                    Remove
                  </button>
                </div>
                <p className="mt-1 text-xs text-slate-500">Why {t.priority}: {t.priority_reason}</p>
                {t.needs_manual_review && (
                  <p className="mt-1 text-xs font-medium text-amber-700">Needs manual review: the AI answer could not be used for this task.</p>
                )}
                {t.task_type === "email" && <p className="mt-1 text-xs text-indigo-700">Email task (drafting and approval come in a later milestone)</p>}
                {dependencyLabel(t) && <p className="mt-1 text-xs text-red-700">{dependencyLabel(t)}</p>}
                <textarea
                  value={t.problem_statement}
                  onChange={(e) => update(i, { problem_statement: e.target.value })}
                  rows={4}
                  className="mt-2 w-full rounded-md border border-slate-300 p-2 text-sm"
                  aria-label="Problem statement"
                />
                <div className="mt-2 grid gap-2 sm:grid-cols-3">
                  <label className="text-xs text-slate-600">
                    Deadline
                    <input
                      type="datetime-local"
                      value={toLocalInput(t.deadline)}
                      onChange={(e) => update(i, { deadline: e.target.value ? new Date(e.target.value).toISOString() : null })}
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    Estimate (minutes)
                    <input
                      type="number"
                      min={1}
                      max={960}
                      value={t.estimated_minutes ?? ""}
                      onChange={(e) => update(i, { estimated_minutes: e.target.value ? Number(e.target.value) : null })}
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                    />
                  </label>
                  <label className="text-xs text-slate-600">
                    Reviewer email
                    <input
                      type="email"
                      value={t.reviewer_email ?? ""}
                      onChange={(e) => update(i, { reviewer_email: e.target.value || null })}
                      className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                    />
                  </label>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 flex gap-2">
            <button
              onClick={save}
              disabled={busy || draft.length === 0 || draft.some((t) => !t.title.trim())}
              className="rounded-md bg-indigo-600 px-5 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              Save {draft.length} task{draft.length === 1 ? "" : "s"}
            </button>
            <button onClick={discard} className="rounded-md border border-slate-300 px-5 py-2 text-slate-700 hover:bg-slate-50">
              Discard
            </button>
          </div>
        </section>
      )}

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Your tasks</h2>
        {saved.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">Nothing saved yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-200 rounded-lg border border-slate-200">
            {saved.map((t) => (
              <li key={t.id} className="p-3">
                <div className="flex items-center gap-2">
                  <Badge priority={t.priority} />
                  <span className="font-medium text-slate-900">{t.title}</span>
                  <span className="ml-auto text-xs text-slate-500">{formatDeadline(t.deadline)}</span>
                </div>
                {dependencyLabel(t) && <p className="mt-1 text-xs text-red-700">{dependencyLabel(t)}</p>}
                {t.problem_statement && <p className="mt-1 whitespace-pre-line text-xs text-slate-600">{t.problem_statement}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
