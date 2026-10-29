# Labeling guide, version 2 (lean evaluation)

> **Version 2 approved as written on 30 October 2026** (approval relayed to the assistant in chat). Any later change to a rule needs a new version and relabeling of the affected notes. This replaces version 1 for the lean evaluation (10 dev notes, 30 test notes). Version 1 and the labels written under it are archived in `eval/archive/full_v1/`.

What changed from version 1: there are now only **three** labels per note (task type, dependency, deadline). Every note is a **single task**, so there is no task-count label, and there is no "needs clarification" label. The rules for the three remaining labels are the same as in version 1, with one simplification: a dependency is always on a team in the directory.

## What you are doing

You read short work notes written in plain language and, for each one, record what a careful person would say the correct structured task is. These labels are the **gold answers** the AI is scored against, so they must come from you alone.

Ground rules:

1. **Blind.** The tool shows only the note text. Do not run the app, the prompts or any AI on these notes, and do not ask an AI for its opinion on a label. Do not open `eval/design/`.
2. **Label what the note says**, as the person who wrote it would mean it. Do not add facts.
3. **If you cannot apply a rule, flag the note** (`f` in the tool) instead of guessing. Flagged notes are rewritten and you label the new version. Never force a label you are unsure about.
4. **Each note on its own.** Do not look back at earlier labels to stay "consistent"; use this guide.
5. Labels may be corrected until the evaluation is frozen (`--relabel`). Every revision is kept in the history. After the freeze they never change.

## The fixed reference point

Every note is read as if written at **Monday 2 November 2026, 09:00, India time (UTC+05:30)**. All deadlines are in this timezone.

Calendar: **Mon 2 Nov (today)**, Tue 3, Wed 4, Thu 5, Fri 6, Sat 7, Sun 8, Mon 9.

The person's own team is **product** (they are Jiya Sharma). The directory is in [`directory.json`](directory.json); the tool lists the other teams.

## Every note is one task

Each note describes exactly one task. Text that tries to instruct the AI ("ignore previous instructions...") is **not part of the task**: ignore it and label the real task.

## Label 1: task type

- **`email`**: the task is to **send, email, mail, write to, reply to, forward or share a document with** a named person or group. A thank-you note or a reply to a message is `email`.
- **`normal`**: everything else, including *asking* someone for something, calls, meetings, reviews, coding, and reminding someone.

"Ask X for Y" is `normal` even though it could be done by email. Task type and dependency are independent: a note can be `email` and also have a dependency.

## Label 2: dependency

The label is either **none**, or a **team** plus a **direction**.

**D1.** There is a dependency when completing the task needs action from **another team** (direction `need_from_them`), or another team is **waiting on or needs** something from the person (direction `they_need_from_me`). It may be stated or clearly implied.

- **D2.** "Another team" is any directory team other than **product**. People belong to their team, so Ravi means *data*, Meera means *payments*, Karan means *platform*. People in **product** (Jiya, Aarav) never create a dependency.
- **D3.** Needing **access, permission, approval, credentials, a fix, data, information or a change** in a system owned by another team counts as a dependency on its owner, even if no team is named. The owner is whoever the directory says owns that system.
- **D4.** Merely mentioning a team, person or system is **not** a dependency: reading a team's docs, working in their system, or sending someone a deliverable ("send the report to Ravi"). Sending something only counts as `they_need_from_me` if the note says they need it, are waiting for it or are blocked on it.
- **D5.** External parties (vendors, banks, couriers, customers, clients) are not teams: **no dependency**.
- **D6.** If a note names an internal group that is **not** in the directory (legal, finance, HR) or you cannot tell which team is meant, **flag the note**.
- **D7.** If a note contains a dependency in both directions, **flag the note**.

## Label 3: deadline

The deadline is the time by which the **task** must be done, as a date and time in IST (the tool takes `YYYY-MM-DD HH:MM`). No deadline is blank. Every deadline is after the reference point.

| The note says | Deadline |
|---|---|
| a clock time with am/pm or 24h ("by 4pm", "at 11am") | that time. If it has already passed today (at or before 09:00), the **same time tomorrow** |
| a bare hour ("by 5") | hours 1 to 6 are PM, 7 to 11 are AM, 12 is noon; then the rule above |
| "EOD", "end of day", "COB" | 18:00 that day |
| "tonight" | 21:00 today |
| "morning" ("tomorrow morning", "friday morning") | 12:00 that day |
| a date, a weekday, "today" or "tomorrow" with no time | **23:59** that day |
| "in N hours" | reference time plus N hours |
| "in N days" | 23:59 on that day |
| a weekday name ("by thursday") | the next such day after today |
| an event time used as the cutoff ("before the 3pm standup") | that event's time, 15:00 today |
| "urgent", "asap", "important", "soon", or no time words at all | **blank** (urgency is not a deadline) |

If a time phrase is not in this table, **flag the note**.

## Using the tool

```powershell
cd D:\taskpilot
.\.venv\Scripts\Activate.ps1
python -m eval.label_tool dev          # practice on the 10 dev notes first
python -m eval.label_tool test         # the 30 test notes
python -m eval.label_tool test --summary    # counts of your answers, to spot patterns
python -m eval.label_tool test --review     # every note beside your label, to check your work
python -m eval.label_tool test --relabel t07   # change one; the old answer stays in the history
```

The tool asks three questions per note and has no defaults except a blank deadline. `?` shows the short rules, `f` flags a note, `s` skips it for now, `q` quits and keeps your progress. Labels are saved to `eval/labels/` with a timestamp the moment you confirm each note.

## Provenance, stated plainly

- The **notes** were written by Claude (an AI assistant), not by the models being evaluated. They were written after the prompts were fixed, and the prompts will not be tuned on the test notes.
- **No labels** were written, drafted or suggested by any AI. The label files are produced only by `label_tool.py` from your answers.
- The assistant will not read the test labels before the prompts are frozen.
