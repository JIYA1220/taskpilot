import type { AppNotification } from "./api";

/** Thin wrapper over the Web Notifications API. It only works after the person grants
 * permission, and browsers only allow asking from a click, never automatically. */
export type Permission = NotificationPermission | "unsupported";

export function permission(): Permission {
  return typeof window !== "undefined" && "Notification" in window ? Notification.permission : "unsupported";
}

export async function requestPermission(): Promise<Permission> {
  if (permission() === "unsupported") return "unsupported";
  return Notification.requestPermission();
}

export function title(n: AppNotification): string {
  if (n.kind === "reminder") {
    const total = n.payload?.total ?? 0;
    return `TaskPilot: ${total} pending task${total === 1 ? "" : "s"}`;
  }
  if (n.kind === "missed") return "TaskPilot: deadline missed";
  if (n.kind === "blocked") return "TaskPilot: a task is blocked";
  if (n.kind === "unblocked") return "TaskPilot: a task is unblocked";
  if (n.kind === "dependency") return "TaskPilot: another team is waiting on you";
  if (n.kind.startsWith("review_")) return "TaskPilot: a task you review";
  if (n.kind === "email_failed") return "TaskPilot: an email could not be sent";
  return "TaskPilot";
}

export function show(n: AppNotification): boolean {
  if (permission() !== "granted") return false;
  try {
    // `tag` makes the browser replace, not stack, a notification it already shows for this id.
    new Notification(title(n), { body: n.message, tag: `taskpilot-${n.id}` });
    return true;
  } catch {
    return false;
  }
}

const LAST_SHOWN_KEY = "taskpilot_last_notified_id";

export function lastShownId(): number | null {
  try {
    const raw = localStorage.getItem(LAST_SHOWN_KEY);
    return raw === null ? null : Number(raw);
  } catch {
    return null;
  }
}

export function setLastShownId(id: number): void {
  try {
    localStorage.setItem(LAST_SHOWN_KEY, String(id));
  } catch {
    /* storage unavailable: worst case a notification is shown again */
  }
}
