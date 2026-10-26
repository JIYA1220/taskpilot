import { useCallback, useEffect, useState } from "react";
import { ApiError, api, Draft, DirectoryTeam } from "./api";

type Props = {
  taskId: number;
  /** Called after anything that may change the task or its emails (a draft was sent, etc.). */
  onChanged: () => void;
  onUnauthorized: () => void;
};

/** The approve-before-send step for an email task. Nothing leaves the building until the person presses Approve. */
export default function DraftPanel({ taskId, onChanged, onUnauthorized }: Props) {
  const [draft, setDraft] = useState<Draft | null | undefined>(undefined);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [people, setPeople] = useState<DirectoryTeam[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const d = await api.getDraft(taskId);
      setDraft(d);
      if (d) {
        setTo(d.to_email ?? "");
        setSubject(d.subject);
        setBody(d.body);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      else setError("Could not load the draft.");
    }
  }, [taskId, onUnauthorized]);

  useEffect(() => {
    load();
    api.directory().then(setPeople).catch(() => undefined);
  }, [load]);

  async function run(work: () => Promise<Draft>) {
    setBusy(true);
    setError("");
    try {
      const d = await work();
      setDraft(d);
      setTo(d.to_email ?? "");
      setSubject(d.subject);
      setBody(d.body);
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      else setError(e instanceof ApiError ? e.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  const save = () => api.saveDraft(taskId, { to_email: to.trim() || null, subject, body });
  const approve = async () => {
    await save(); // send exactly what is on screen, including edits not yet saved
    return api.approveDraft(taskId);
  };

  if (draft === undefined) return <p className="mt-2 text-xs text-slate-500">Loading draft...</p>;

  const box = "mt-3 rounded-md border border-indigo-200 bg-indigo-50/40 p-3";

  if (draft === null || draft.state === "rejected") {
    return (
      <div className={box}>
        {draft?.state === "rejected" && <p className="mb-2 text-xs text-slate-600">The previous draft was rejected and nothing was sent.</p>}
        <button
          disabled={busy}
          onClick={() => run(() => api.createDraft(taskId))}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {busy ? "Drafting..." : "Write a draft with AI"}
        </button>
        <p className="mt-2 text-xs text-slate-500">You will review and approve it before anything is sent.</p>
        {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
      </div>
    );
  }

  if (draft.state === "sent") {
    return (
      <div className={box}>
        <p className="text-sm font-medium text-green-800">Sent to {draft.to_email}.</p>
        <p className="mt-1 text-xs text-slate-600">Subject: {draft.subject}</p>
      </div>
    );
  }

  if (draft.state === "approved") {
    return (
      <div className={box}>
        <p className="text-sm font-medium text-amber-800">Approved, but not delivered yet. TaskPilot keeps retrying automatically.</p>
        <p className="mt-1 text-xs text-slate-600">To {draft.to_email}, subject: {draft.subject}. The status appears under the task.</p>
      </div>
    );
  }

  const hasPlaceholder = body.toLowerCase().includes("[attach");
  const canSend = !busy && to.trim() !== "" && subject.trim() !== "" && body.trim() !== "" && !hasPlaceholder;
  const directoryEmails = people.flatMap((t) => t.members);
  const fromDirectory = directoryEmails.some((m) => m.email === to.trim().toLowerCase());

  return (
    <div className={box}>
      {draft.needs_manual_review && (
        <p className="mb-2 rounded bg-amber-50 p-2 text-xs text-amber-800">The AI draft could not be used. Please write the email yourself.</p>
      )}
      <label className="block text-xs text-slate-600">
        To
        <input
          list={`people-${taskId}`}
          value={to}
          onChange={(e) => setTo(e.target.value)}
          placeholder="Choose a colleague or type an address"
          className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
        />
        <datalist id={`people-${taskId}`}>
          {directoryEmails.map((m) => (
            <option key={m.id} value={m.email}>{m.name}</option>
          ))}
        </datalist>
      </label>
      {to.trim() === "" && <p className="mt-1 text-xs text-amber-700">Nobody was named in the task. Choose who this goes to.</p>}
      {to.trim() !== "" && !fromDirectory && <p className="mt-1 text-xs text-slate-500">This address is not in the directory. It will be used exactly as typed.</p>}
      <label className="mt-2 block text-xs text-slate-600">
        Subject
        <input value={subject} maxLength={150} onChange={(e) => setSubject(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm" />
      </label>
      <label className="mt-2 block text-xs text-slate-600">
        Message
        <textarea value={body} rows={7} maxLength={4000} onChange={(e) => setBody(e.target.value)} className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm" />
      </label>
      {hasPlaceholder && (
        <p className="mt-1 text-xs text-amber-700">Remove the [Attach: ...] line first. TaskPilot cannot attach files.</p>
      )}
      <p className="mt-1 text-xs text-slate-500">A footer "Sent by TaskPilot on behalf of you" is added when it is sent.</p>
      {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button disabled={!canSend} onClick={() => run(approve)} className="rounded-md bg-green-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-800 disabled:opacity-50">
          Approve and send
        </button>
        <button disabled={busy} onClick={() => run(save)} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50">
          Save edits
        </button>
        <button disabled={busy} onClick={() => run(() => api.rejectDraft(taskId))} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50">
          Reject
        </button>
      </div>
    </div>
  );
}
