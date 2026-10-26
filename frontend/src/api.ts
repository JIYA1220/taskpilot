export type Question = {
  id: string;
  task_index: number;
  task_title: string;
  field: string;
  question: string;
  suggestions: string[];
};

export type Dependency = { direction: string; team: string; what_is_needed: string };

export type Proposed = {
  source_text: string;
  title: string;
  task_type: "normal" | "email";
  deadline: string | null;
  estimated_minutes: number | null;
  priority: "P1" | "P2" | "P3";
  priority_reason: string;
  problem_statement: string;
  reviewer_email: string | null;
  dependency_hint: Dependency;
  needs_manual_review: boolean;
  clarifications: { question: string; answer: string | null }[];
};

export type Outcome = {
  thread_id: string;
  status: "needs_answers" | "ready";
  extracted_titles: string[];
  questions: Question[];
  tasks: Proposed[];
  warnings: string[];
};

export type SavedTask = {
  id: number;
  title: string;
  task_type: string;
  problem_statement: string;
  priority: string;
  priority_reason: string;
  deadline: string | null;
  status: "todo" | "in_progress" | "blocked" | "done" | "missed";
  reviewer_email: string | null;
  dependency_hint: Dependency | null;
  needs_manual_review: boolean;
  completed_at: string | null;
};

export type AppNotification = {
  id: number;
  kind: "reminder" | "missed" | string;
  message: string;
  payload: { total?: number; task_id?: number } | null;
  read: boolean;
  created_at: string;
};

export type NotificationList = { unread: number; items: AppNotification[] };

export type Prefs = {
  reminders_enabled: boolean;
  interval_minutes: number;
  work_start: string;
  work_end: string;
  work_days: number[];
};

export type PrefsOut = Prefs & { timezone: string; last_reminder_at: string | null };

export type Me = { id: number; name: string; email: string; team_id: string };

const TOKEN_KEY = "taskpilot_token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session just will not persist */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) detail = String(body.detail[0].msg).replace(/^Value error, /, "");
      else detail = "The request was not valid.";
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string }>("/api/auth/login", post({ email, password })),
  me: () => request<Me>("/api/auth/me"),
  organize: (text: string) => request<Outcome>("/api/organize", post({ text })),
  answer: (threadId: string, answers: Record<string, string | null>) =>
    request<Outcome>(`/api/organize/${encodeURIComponent(threadId)}/answers`, post({ answers })),
  saveTasks: (tasks: Proposed[]) => request<SavedTask[]>("/api/tasks", post({ tasks })),
  listTasks: () => request<SavedTask[]>("/api/tasks"),
  setStatus: (id: number, status: "todo" | "in_progress" | "done") =>
    request<SavedTask>(`/api/tasks/${id}/status`, post({ status })),
  notifications: () => request<NotificationList>("/api/notifications"),
  markRead: (id: number) => request<void>(`/api/notifications/${id}/read`, { method: "POST" }),
  markAllRead: () => request<void>("/api/notifications/read-all", { method: "POST" }),
  getPrefs: () => request<PrefsOut>("/api/settings/reminders"),
  putPrefs: (prefs: Prefs) => request<PrefsOut>("/api/settings/reminders", { method: "PUT", body: JSON.stringify(prefs) }),
};
