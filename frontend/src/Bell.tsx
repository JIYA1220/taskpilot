import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, AppNotification } from "./api";
import { lastShownId, permission, requestPermission, setLastShownId, show, Permission } from "./browserNotify";

const POLL_MS = 20_000;

function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

type Props = {
  onUnauthorized: () => void;
  /** Called when a new notification arrives, so the task list can refresh (a task may have become missed). */
  onNew: () => void;
};

export default function Bell({ onUnauthorized, onNew }: Props) {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [perm, setPerm] = useState<Permission>(permission());
  const box = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    try {
      const data = await api.notifications();
      setItems(data.items);
      setUnread(data.unread);
      const newest = data.items.reduce((max, n) => Math.max(max, n.id), 0);
      const seen = lastShownId();
      if (seen === null) {
        // First visit in this browser: do not replay history as pop-ups.
        setLastShownId(newest);
        return;
      }
      const fresh = data.items.filter((n) => n.id > seen && !n.read).sort((a, b) => a.id - b.id);
      if (fresh.length) {
        fresh.forEach((n) => show(n));
        setLastShownId(newest);
        onNew();
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onUnauthorized();
      // Other errors (server restarting, offline): stay quiet and try again on the next poll.
    }
  }, [onNew, onUnauthorized]);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => clearInterval(timer);
  }, [poll]);

  useEffect(() => {
    function outside(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", outside);
    document.addEventListener("click", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("click", outside);
    };
  }, []);

  async function read(id: number) {
    await api.markRead(id).catch(() => undefined);
    poll();
  }

  async function readAll() {
    await api.markAllRead().catch(() => undefined);
    poll();
  }

  async function enableBrowser() {
    setPerm(await requestPermission());
  }

  return (
    <div className="relative" ref={box}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={`Notifications, ${unread} unread`}
        className="relative rounded-md border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50"
      >
        Notifications
        {unread > 0 && (
          <span className="absolute -right-2 -top-2 min-w-5 rounded-full bg-red-600 px-1.5 text-center text-xs font-semibold text-white">
            {unread}
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-2 w-96 max-w-[90vw] rounded-lg border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
            <span className="text-sm font-semibold text-slate-900">Notifications</span>
            <button onClick={readAll} disabled={unread === 0} className="text-xs text-indigo-700 hover:underline disabled:text-slate-400">
              Mark all read
            </button>
          </div>
          {perm === "default" && (
            <div className="border-b border-slate-200 bg-indigo-50 px-3 py-2 text-xs text-indigo-900">
              Get a pop-up when a reminder arrives.{" "}
              <button onClick={enableBrowser} className="font-semibold underline">
                Enable browser notifications
              </button>
            </div>
          )}
          {perm === "denied" && (
            <div className="border-b border-slate-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              Browser notifications are blocked for this site. Allow them in the browser's site settings to get pop-ups.
            </div>
          )}
          <ul className="max-h-96 overflow-y-auto">
            {items.length === 0 && <li className="px-3 py-4 text-sm text-slate-500">Nothing yet.</li>}
            {items.map((n) => (
              <li key={n.id} className={`border-b border-slate-100 px-3 py-2 ${n.read ? "bg-white" : "bg-indigo-50/50"}`}>
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span
                    className={`rounded px-1.5 py-0.5 font-semibold ${n.kind === "missed" ? "bg-red-100 text-red-800" : "bg-slate-200 text-slate-700"}`}
                  >
                    {n.kind === "missed" ? "Missed" : "Reminder"}
                  </span>
                  <span>{ago(n.created_at)}</span>
                  {!n.read && (
                    <button onClick={() => read(n.id)} className="ml-auto text-indigo-700 hover:underline">
                      Mark read
                    </button>
                  )}
                </div>
                <p className="mt-1 whitespace-pre-line text-sm text-slate-800">{n.message}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
