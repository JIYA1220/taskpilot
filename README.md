# TaskPilot

I type my day's tasks in plain language ("ask payments team for the refund API", "send weekly report to Ravi by 5"). I press **Organize**. The AI turns the text into structured tasks, asks me a few short questions where it is unsure (priority, deadline, who it depends on), then rewrites each task as a clear problem statement. It reminds me of pending tasks every 60 minutes. Each task can have a **reviewer** who gets an email when it is completed or when the deadline is missed. Tasks that only mean "send an email" get a drafted email that I approve with one click. Tasks that depend on another team are flagged as **blocked**, the AI asks me what is missing, notifies the reviewer, and opens a chat room with the people who own that dependency.

All AI calls go to the [Groq](https://groq.com) API (`llama-3.3-70b-versatile` and `llama-3.1-8b-instant`). There is no local model.

## Status

Work in progress. Nothing below has been measured yet: **evaluation results: not yet measured.**

| Milestone | State |
|---|---|
| M0 Scaffolding, auth, demo organization | done |
| M1 Organize (split, clarify, rewrite) | not started |
| M2 Reminders and deadlines | not started |
| M3 Reviewer emails and approve-before-send | not started |
| M4 Dependencies and schedule conflicts | not started |
| M5 Team chat | not started |
| M6 Evaluation on hand-labeled data | not started |
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
```

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
