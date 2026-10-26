# TaskPilot

I type my day's tasks in plain language ("ask payments team for the refund API", "send weekly report to Ravi by 5"). I press **Organize**. The AI turns the text into structured tasks, asks me a few short questions where it is unsure (priority, deadline, who it depends on), then rewrites each task as a clear problem statement. It reminds me of pending tasks every 60 minutes. Each task can have a **reviewer** who gets an email when it is completed or when the deadline is missed. Tasks that only mean "send an email" get a drafted email that I approve with one click. Tasks that depend on another team are flagged as **blocked**, the AI asks me what is missing, notifies the reviewer, and opens a chat room with the people who own that dependency.

All AI calls go to the [Groq](https://groq.com) API (`openai/gpt-oss-120b` as the main model and `openai/gpt-oss-20b` as the fast one; model names are config, not code). There is no local model. The original plan used two Llama models, which are no longer available on the Groq account used here.

## Status

Work in progress. Nothing below has been measured yet: **evaluation results: not yet measured.**

| Milestone | State |
|---|---|
| M0 Scaffolding, auth, demo organization | done |
| M1 Organize (split, clarify, rewrite) | done (tested with a mocked LLM and tried by hand against Groq; accuracy: not yet measured) |
| M2 Reminders and deadlines | done (fake-clock tests; in-app bell, settings and task buttons tried live; browser pop-ups implemented but not yet seen in a real browser) |
| M3 Reviewer emails and approve-before-send | done for the local test inbox only (SMTP tested against a real local server; tried live end to end). **Real Gmail sending is not built yet** |
| M4 Dependencies and schedule conflicts | done (tested with a mocked LLM and pure unit tests; tried live in the UI). Dependency detection accuracy: not yet measured |
| M5 Team chat | not started |
| M6 Evaluation on hand-labeled data | in progress: labeling guide, blind labeling tool, the 15 dev and 60 test notes, and the runner, scoring and freeze are built and tested with a scripted fake model; **no gold labels written yet; all results: not yet measured** |
| M7 README, demo, deployment notes | not started |

## Run it

Needs Python 3.11+, Node 20+ and Docker Desktop.

```powershell
copy .env.example .env          # then fill in GROQ_API_KEY (JWT_SECRET is optional in development)
docker compose up --build       # backend :8000, frontend :5173, Mailpit inbox :8025
docker compose exec backend python -m scripts.seed
```

Open http://localhost:5173. The seeded demo organization is synthetic (4 teams, 8 users, defined in [`eval/directory.json`](eval/directory.json)). Log in as `jiya.sharma@acme.example.com` with the demo password `taskpilot-demo`. These accounts exist only on your machine.

### Without Docker

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m scripts.seed
uvicorn backend.main:app --reload          # API on :8000
cd frontend; npm install; npm run dev      # UI on :5173
```

### Useful commands

```powershell
pytest                                   # backend tests (no network, no .env needed)
python -m scripts.install_hooks          # block commits containing a Groq key (run once per clone)
python -m scripts.check_models           # confirm the configured Groq models exist (uses .env)
python -m scripts.gen_secret             # print a JWT secret to paste into .env
python -m eval.label_tool dev            # write gold labels by hand (blind: shows only the note text)
python -m eval.label_tool test --stats   # labeling progress
```

## Evaluation data and labeling

The evaluation uses 15 dev notes (for prompt tuning) and 60 test notes (never used for tuning), in [`eval/dev.jsonl`](eval/dev.jsonl) and [`eval/dataset.jsonl`](eval/dataset.jsonl). The notes were written by an AI assistant, not by the evaluated models. **Every gold label is written by a person** with [`eval/label_tool.py`](eval/label_tool.py), which shows only the note text, and follows [`eval/LABELING_GUIDE.md`](eval/LABELING_GUIDE.md). Labels are stored append-only with timestamps and revision numbers in `eval/labels/`. Fifty of the test notes contain one task each and ten contain several; per-task metrics are scored on the fifty single-task notes only, so the confidence intervals rest on n=50.

## How Organize works (Milestone 1)

```
notes ──► Step A: extract (main model) ──► Step B: clarify (code) ──► PAUSE ──► Step C: rewrite (main model) ──► review ──► save
```

1. **Step A, extract.** The main model splits the notes into tasks and returns title, type (`normal` or `email`), deadline (relative dates resolved against the current time in the configured timezone), estimated minutes, a possible dependency, and a list of unknowns with tappable suggestions. The dependency team is restricted to the demo directory's team ids, so the model cannot invent a team.
2. **Step B, clarify.** Plain code, not an LLM call: it picks at most 3 questions from the unknowns (dependency, then deadline, then priority, then the rest) and the LangGraph run **pauses** at an interrupt. The paused run is stored in a SQLite checkpoint, so it survives a server restart. Every question can be skipped.
3. **Step C, rewrite.** The main model writes goal, definition of done and inputs needed, assigns P1 to P3 with a one-line reason, and may set a deadline only when the person's answer gave one. The deadline line is added by code, not by the model.
4. **Review and save.** Nothing is stored until the person edits and saves.

Model output is requested in JSON mode and validated with Pydantic. On a validation failure the model gets one retry with the error message; after that the task falls back to "needs manual review" instead of crashing. A reviewer address is only ever taken from the person's own answer or the directory, never from model output. Every LLM attempt is recorded in an audit table (model, prompt version, latency, tokens, parsed ok), never the key or the message text.

Prompts are versioned files in [`backend/prompts/`](backend/prompts/). The date rules in them ("by 5" is 17:00 today or tomorrow if already past, a date alone is 23:59) are the conventions the evaluation's labeling guide will use.

## Running the evaluation

The order, and why:

1. **Label** the dev notes, then the test notes, by hand (`python -m eval.label_tool dev`, then `test`). Nothing below can produce a reported number without them.
2. **Tune prompts on the dev set only.** `python -m eval.run_eval run --set dev` is free to run as often as you like (its output goes to `eval/results/dev/`, which git ignores, and is labeled "NOT a reported result").
3. **Commit everything, then freeze:** `python -m eval.freeze`. It refuses unless every test note is labeled and every file the result depends on is committed, then records their exact committed identities (prompts, matcher and extraction code, the directory, the test notes, the labels, the scoring code and the runner) in `eval/results/freeze.json`.
4. **Check the cost first:** `python -m eval.run_eval estimate --set test --daily-token-cap N` (N from the Groq console) says how many tokens and calls the run needs and whether it fits in a day.
5. **Run once:** `python -m eval.run_eval run --set test`. It refuses to start if the freeze no longer holds, so a prompt, code or label change forces a new freeze, and the earlier results are kept under `eval/results/superseded/` with a note that they are superseded.

What a run does: every note goes through the app's real extraction step and team matcher with **both** models (each doing every job), at temperature 0 against the fixed reference time. Every reply is cached (`eval/cache/`, git-ignored), keyed on the model, the full prompt, the date and the settings, so a stopped run resumes where it stopped and a rerun costs nothing. A rate-limit, quota or network error stops the run cleanly with exit code 3. It is never scored as a wrong answer. It reports how many API calls it made and the cache hit rate.

**Metrics** (each with a 95% interval: Wilson for proportions, a percentile bootstrap over notes for F1, medians, p95 and means), written to `eval/results/metrics.json` and `summary.md`:
- JSON valid on the first try, and after the one retry.
- Task type accuracy, deadline exact match (same instant), and task-count match on the multi-task notes.
- Dependency detection: precision, recall, F1, and direction accuracy among the true positives. A "predicted dependency" is any task whose dependency direction is not `none`.
- **Team routing, top-1,** on single-task notes whose gold dependency names a directory team, for three methods on the same notes: keyword-only (the best team; a tie or no evidence counts as wrong), the extract step's own pick, and keyword evidence plus the model (the matcher the app actually uses).
- Needs clarification (predicted = the model asked at least one question): accuracy, precision, recall.
- Latency (median and p95 of the extract call; latency is the original call's, reused on cached reruns), and mean tokens per note and per predicted task, with reasoning tokens shown separately.

`eval/ERROR_ANALYSIS.md` lists every wrong note for the main model with a factual one-line category per wrong field, and counts by category. No explanations are written.

**Scope.** Per-task metrics use only the notes labeled as a single task (50 of the 60 test notes), so the intervals rest on n=50 and are wide. For those notes the model's first predicted task is compared with the gold label, and a different number of tasks is counted as a task-count error. Other limits: one person labeled everything; the demo organization and notes are synthetic; the two models are two sizes of one family; the notes were written by the assistant that also wrote the prompts, although the prompts are not tuned on the test notes.

## Reminders and deadlines (Milestone 2)

- **Missed deadlines.** A background job runs every 30 seconds (`SCHEDULER_TICK_SECONDS`). Any task that is not done and whose deadline has passed becomes `missed`, and its owner gets one notification. A deadline exactly equal to "now" is not missed yet.
- **Reminders.** Every `interval` minutes (default 60, settable from 1 to 1440 in Settings; the "Demo: 1 min" button is for trying it out) the owner gets one notification listing pending tasks: **P1 first, then nearest deadline, tasks without a deadline last**, each with time left, at most 8 shown. No pending tasks means no reminder.
- **Working hours.** Reminders are only sent inside the working hours and days chosen in Settings (default Monday to Friday, 09:00 to 18:00 Asia/Kolkata; start inclusive, end exclusive). After hours nothing is sent, and the first tick in the next working window reminds straight away. Missed-deadline detection is a fact, not a nudge, so it ignores working hours.
- **Where you see them.** The Notifications button (bell) polls every 20 seconds and shows unread counts. If you click "Enable browser notifications" (browsers only allow asking after a click), new notifications also pop up through the Web Notifications API.
- **Finishing tasks.** Start, Pause, Done and Reopen buttons call `POST /api/tasks/{id}/status`. `blocked` and `missed` are set by the system only; a missed task can only be marked done (late).
- **Tests.** The logic in `backend/reminders.py` takes `now` as an argument, so the tests drive it with a fake clock: first reminder, 59 versus 60 minutes, custom intervals, working-hour edges, weekends, UTC versus IST, resuming the next morning, missed-once, per-user isolation.

Limitations: the scheduler is **in-process and single-process** (run one backend process, or set `SCHEDULER_ENABLED=false` on extras, otherwise reminders would repeat); notifications are polled, not pushed; working hours use one app-wide timezone and do not support overnight shifts.

## Email: reviewers and approve-before-send (Milestone 3)

**What is built.** Everything goes through an `EmailSender` interface. The only implementation so far is `MailpitSender`, plain SMTP to a local inbox (Mailpit in Docker, or `python -m scripts.dev_mailbox`, a small stand-in on the same ports for machines without Docker). It uses the standard library's `smtplib` instead of `aiosmtplib`, because the app sends from ordinary threads and async would add nothing. `EMAIL_BACKEND=gmail` is deliberately **not available yet** and fails at startup with a clear message. No code in the repo can reach a real mailbox.

**Reviewer emails.** A task can have a reviewer. When it is completed, or its deadline is missed, the reviewer gets an email with the title, problem statement, deadline and outcome ("completed late" is stated as such), and a footer "Sent by TaskPilot on behalf of <name>". If the reviewer is also a TaskPilot user they get an in-app notification too. Each event is sent **once**: the `email_event` table has a unique constraint on (task, kind), so a duplicate is impossible at the database level, not just in code.

**The outbox.** The email is written to the database in the same transaction as the status change, and a separate step sends it. A failed send is retried with exponential backoff (1, 2, 4, 8 minutes by default, 5 attempts), shown under the task in the UI, and, if it finally fails, reported to the owner as a notification with a Retry button. Nothing is dropped silently. A row is claimed atomically before sending, so the request thread and the scheduler never send the same row, and a claim left by a crashed worker is released after 10 minutes. Delivery is **at-least-once**: a crash between "server accepted the mail" and "row marked sent" could repeat one email.

**Email tasks: draft, approve, send.** For a task of type `email` the AI writes a draft; you edit it, then approve, edit or reject. Only an approved draft is sent. Sending marks the task done and notifies the reviewer.
- **Who it goes to is decided by code, never by the model.** A person named in the title or your original note is matched to the directory (a full name, or a first name that identifies exactly one person). If nobody or several people match, you choose. The AI-written statement is only a fallback, and the reviewer is ignored there. An address you type is used exactly as typed and flagged as outside the directory.
- **The draft prompt sees only the title, your original words and the goal**, not the rest of the AI-written statement. (A live test showed the model inventing "Meera has reviewed the report" from a later step in that statement.)
- **No links or addresses in model text.** A draft containing a URL or an email address is rejected and the model is asked once to rewrite it; after that you write it by hand.
- **No pretending to attach.** If a file would normally be attached, the draft ends with a `[Attach: ...]` line, and approval is blocked until you remove it, because TaskPilot cannot attach files.
- **Task text is untrusted in emails.** HTML is escaped, subjects and names are flattened to one line (no header injection), and the templates add no links.

**Auto-send, and why it is off by default.** An AI should not email people on your behalf without your say-so, so the default is: draft, then wait for approval. You can turn on "auto-send" in Settings. Even then it only sends a draft whose recipient was matched from the directory **and** whose address is exactly in the organization's domain (`ORG_DOMAIN`; look-alikes such as `evilacme.example.com` do not count). Anything outside the domain, without a recipient, with an `[Attach]` line or with an unusable AI draft always waits for you.

Not built yet: the real Gmail sender, the recipient allowlist that will guard it, and the `make`-free login command for it. Those come in the next step.

## Dependencies and schedule conflicts (Milestone 4)

Two kinds of conflict, both shown with a red flag on the task.

**1. Dependency conflict (AI-assisted).** The extract step (Milestone 1) already says whether a task needs something from another team, or another team needs something from you. When such a task is saved:
- A `Dependency` record is created (what is needed, which team, direction, by when, who you have already asked, and how the team was chosen).
- **A task that needs something from another team becomes `blocked`.** Its owner is notified, and **its reviewer is emailed** that it is blocked and why (once per task, like the other reviewer emails).
- **A task that another team is waiting on is not blocked.** You can act on it right now, so calling it blocked would be wrong; it gets the record, a notice and the red flag only. (This is a deliberate choice: the original idea grouped both cases under "blocked".)
- You are asked three follow-up questions in the task card: what exactly is needed, by when, and who you have already asked. These are a plain form, not an AI call.
- **Mark resolved** closes a dependency. The task is unblocked only when nothing else is blocking it.
- If the AI missed one, **Flag a dependency the AI missed** adds it by hand, with the team chosen by you or worked out for you.

**How the team is chosen.** First by keywords: each team's owned systems, its name and its members are matched against the task text. A word that several teams share (like "dashboard") counts for less, so it cannot decide on its own, and your own team is never a candidate. If one team clearly wins (a score of at least 1.0 and 1.5 times the runner-up), that is the answer and **no model is called**. Otherwise the **fast model** chooses among the top three candidates, and may answer "unknown" rather than guess. If the model cannot help, the extract step's own pick is the fallback. Which method decided (`keyword`, `llm`, `step_a`, `manual` or `none`) is stored with every dependency, along with the extract step's pick, so the evaluation can compare the methods.

**2. Schedule conflict (plain rules, no AI).** Arithmetic on estimates and deadlines, so it is deterministic and explainable.
- **Overload:** the estimated minutes of today's open tasks (those due today that you can work on, so not blocked, done or missed) exceed the working time left today. Working time comes from your Settings (start inclusive, end exclusive; zero on a day off or after hours). A task without an estimate counts as 0 minutes and is named in the message.
- **P1 clash:** two or more P1 tasks with exactly the same deadline.
- **Suggestions, never actions.** For an overload it suggests moving the largest P3 task first, then more P3 tasks only as needed, then P2 tasks only if P3 cannot free enough, **never a P1**, each to the same time on the next working day. The red box offers "Move to ..." (which you press, or ignore with "Keep as it is"). If even moving every P2 and P3 task would not fit, it says so. For a P1 clash it only names the clash and the shortest task.

Limitations: team matching depends on the demo directory's keywords and a three-team shortlist; the extract step can miss or invent a dependency (not yet measured); a task's "today" uses estimates you or the AI entered, which are guesses; only one reviewer email per task and kind, so a second dependency does not email again; the overload rule only looks at today.

## Layout

```
backend/    FastAPI app, SQLModel models, auth, tests
frontend/   React + Vite + TypeScript + Tailwind
eval/       demo directory (and, later, the labeled evaluation sets)
scripts/    seed, hooks and helper commands (run as python -m scripts.<name>)
docs/       design notes
```

## Design notes so far

- **Team ids are slugs** (`payments`, not `3`). They are the enum the model must choose from and the value in the gold labels, so they stay readable.
- **`eval/directory.json` is the single source of truth** for the demo organization. It gets frozen (`scripts.freeze_directory`) before labeling starts, and a test fails if it changes afterwards.
- **Real email is off by default.** The `EmailSender` interface (M3) defaults to a local Mailpit inbox; real Gmail sending needs `EMAIL_BACKEND=gmail` and an allowlist, and an empty allowlist blocks every Gmail send.

## Safety

- Secrets live in `.env` and `secrets/`, both gitignored. A pre-commit hook and a CI step block any commit that contains a Groq key prefix or those files.
- Passwords are hashed with argon2. Logins return a signed, expiring JWT.

## License

MIT
