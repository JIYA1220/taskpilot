/** Same wording as the backend's format_time_left, so the list and the reminders agree. */
export function timeLeft(deadlineIso: string | null, nowMs: number): string {
  if (!deadlineIso) return "no deadline";
  const seconds = Math.floor((new Date(deadlineIso).getTime() - nowMs) / 1000);
  if (seconds < 0) return "overdue";
  const minutes = Math.floor(seconds / 60);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days) return `due in ${days}d ${hours}h`;
  if (hours) return `due in ${hours}h ${mins}m`;
  return mins ? `due in ${mins}m` : "due in under a minute";
}

/** ISO time -> the "YYYY-MM-DDTHH:mm" string a datetime-local input expects, in the browser's timezone. */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function formatDeadline(iso: string | null): string {
  if (!iso) return "No deadline";
  return new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
