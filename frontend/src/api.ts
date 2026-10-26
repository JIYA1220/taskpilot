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

export type Draft = {
  id: number;
  task_id: number;
  to_email: string | null;
  to_source: string;
  subject: string;
  body: string;
  state: "draft" | "approved" | "sent" | "rejected";
  needs_manual_review: boolean;
};

export type EmailEvent = {
  id: number;
  task_id: number;
  kind: "completed" | "missed" | "draft" | string;
  recipient: string;
  subject: string;
  state: "pending" | "sending" | "sent" | "failed";
  attempts: number;
  next_attempt_at: string;
  last_error: string;
  sent_at: string | null;
};

export type EmailSettings = { auto_send_enabled: boolean; org_domain: string };

export type DependencyItem = {
  id: number;
  task_id: number;
  depends_on_team_id: string | null;
  team_name: string;
  direction: "need_from_them" | "they_need_from_me";
  what_is_needed: string;
  needed_by: string | null;
  already_asked: string;
  state: "open" | "resolved";
  match_method: string;
  hint_team: string | null;
  created_at: string;
  resolved_at: string | null;
};

export type ConflictSuggestion = { task_id: number; title: string; priority: string; estimated_minutes: number; proposed_deadline: string };

export type Conflict = {
  kind: "overload" | "p1_clash" | string;
  message: string;
  task_ids: number[];
  minutes_needed: number | null;
  minutes_available: number | null;
  suggestions: ConflictSuggestion[];
  untimed_task_ids: number[];
};

export type DirectoryTeam = { id: string; name: string; members: { id: number; name: string; email: string }[] };

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
  directory: () => request<DirectoryTeam[]>("/api/directory"),
  listDependencies: () => request<DependencyItem[]>("/api/dependencies"),
  addDependency: (taskId: number, body: { direction: string; what_is_needed: string; team_id: string | null }) =>
    request<DependencyItem>(`/api/tasks/${taskId}/dependencies`, post(body)),
  updateDependency: (id: number, body: { what_is_needed: string; needed_by: string | null; already_asked: string; team_id: string | null }) =>
    request<DependencyItem>(`/api/dependencies/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  resolveDependency: (id: number) => request<DependencyItem>(`/api/dependencies/${id}/resolve`, { method: "POST" }),
  listConflicts: () => request<Conflict[]>("/api/conflicts"),
  moveDeadline: (taskId: number, deadline: string) => request<SavedTask>(`/api/tasks/${taskId}/deadline`, post({ deadline })),
  createDraft: (taskId: number) => request<Draft>(`/api/tasks/${taskId}/draft`, { method: "POST" }),
  getDraft: (taskId: number) => request<Draft | null>(`/api/tasks/${taskId}/draft`),
  saveDraft: (taskId: number, draft: { to_email: string | null; subject: string; body: string }) =>
    request<Draft>(`/api/tasks/${taskId}/draft`, { method: "PUT", body: JSON.stringify(draft) }),
  approveDraft: (taskId: number) => request<Draft>(`/api/tasks/${taskId}/draft/approve`, { method: "POST" }),
  rejectDraft: (taskId: number) => request<Draft>(`/api/tasks/${taskId}/draft/reject`, { method: "POST" }),
  listEmails: () => request<EmailEvent[]>("/api/emails"),
  retryEmail: (id: number) => request<EmailEvent>(`/api/emails/${id}/retry`, { method: "POST" }),
  getEmailSettings: () => request<EmailSettings>("/api/settings/email"),
  putEmailSettings: (auto_send_enabled: boolean) =>
    request<EmailSettings>("/api/settings/email", { method: "PUT", body: JSON.stringify({ auto_send_enabled }) }),
  getPrefs: () => request<PrefsOut>("/api/settings/reminders"),
  putPrefs: (prefs: Prefs) => request<PrefsOut>("/api/settings/reminders", { method: "PUT", body: JSON.stringify(prefs) }),
};
