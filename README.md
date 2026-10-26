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
| M3 Reviewer emails and approve-before-send | not started |
| M4 Dependencies and schedule conflicts | not started |
| M5 Team chat | not started |
| M6 Evaluation on hand-labeled data | in progress: labeling guide, blind labeling tool and the 15 dev and 60 test notes exist; **no gold labels written yet; all results: not yet measured** |
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

## Reminders and deadlines (Milestone 2)

- **Missed deadlines.** A background job runs every 30 seconds (`SCHEDULER_TICK_SECONDS`). Any task that is not done and whose deadline has passed becomes `missed`, and its owner gets one notification. A deadline exactly equal to "now" is not missed yet.
- **Reminders.** Every `interval` minutes (default 60, settable from 1 to 1440 in Settings; the "Demo: 1 min" button is for trying it out) the owner gets one notification listing pending tasks: **P1 first, then nearest deadline, tasks without a deadline last**, each with time left, at most 8 shown. No pending tasks means no reminder.
- **Working hours.** Reminders are only sent inside the working hours and days chosen in Settings (default Monday to Friday, 09:00 to 18:00 Asia/Kolkata; start inclusive, end exclusive). After hours nothing is sent, and the first tick in the next working window reminds straight away. Missed-deadline detection is a fact, not a nudge, so it ignores working hours.
- **Where you see them.** The Notifications button (bell) polls every 20 seconds and shows unread counts. If you click "Enable browser notifications" (browsers only allow asking after a click), new notifications also pop up through the Web Notifications API.
- **Finishing tasks.** Start, Pause, Done and Reopen buttons call `POST /api/tasks/{id}/status`. `blocked` and `missed` are set by the system only; a missed task can only be marked done (late).
- **Tests.** The logic in `backend/reminders.py` takes `now` as an argument, so the tests drive it with a fake clock: first reminder, 59 versus 60 minutes, custom intervals, working-hour edges, weekends, UTC versus IST, resuming the next morning, missed-once, per-user isolation.

Limitations: the scheduler is **in-process and single-process** (run one backend process, or set `SCHEDULER_ENABLED=false` on extras, otherwise reminders would repeat); notifications are polled, not pushed; working hours use one app-wide timezone and do not support overnight shifts.

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
