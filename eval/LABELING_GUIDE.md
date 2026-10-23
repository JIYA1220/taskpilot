# Labeling guide

Version 1 (draft, waiting for Jiya's approval). One page of rules so that every ambiguous note is labeled the same way. Read it once before labeling, and press `?` in the tool for the short version.

## What you are doing

You read short work notes written in plain language and, for each one, record what a careful person would say the correct structured task is. These labels are the **gold answers** the AI is scored against, so they must come from you alone.

Ground rules:

1. **Blind.** The tool shows only the note text. Do not run the app, the prompts or any AI on these notes, and do not ask an AI for its opinion on a label. Do not open `eval/design/`.
2. **Label what the note says**, as the person who wrote it would mean it. Do not add facts.
3. **If you cannot apply a rule, flag the note** (`f` in the tool) instead of guessing. Flagged notes are rewritten and you label the new version. Never force a label you are unsure about.
4. **Each note on its own.** Do not look back at earlier labels to stay "consistent"; use this guide.
5. Labels may be corrected until the evaluation is frozen. Every revision is kept in the history file. After the freeze (hash recorded before the final run) they never change.

## The fixed reference point

Every note is read as if written at **Monday 2 November 2026, 09:00, India time (UTC+05:30)**. All deadlines are in this timezone.

Calendar: **Mon 2 Nov (today)**, Tue 3, Wed 4, Thu 5, Fri 6, Sat 7, Sun 8, Mon 9.

The person's own team is **product** (they are Jiya Sharma). The directory is in [`directory.json`](directory.json); the tool lists it.

## Label 1: number of tasks

One task per distinct action on a distinct object or recipient.

- **S1.** Bullets, numbered items, semicolons, "also" and "and then" usually start a new task.
- **S2.** Reasons, deadlines and details belong to the task they modify. They are not new tasks.
- **S3.** Preparing something and then sending **that same thing** is **one** task ("write the notes and send them to the team").
- **S4.** Text that tries to instruct the AI ("ignore previous instructions...") is not a task. Ignore it.
- **S5.** Greetings and chatter are not tasks.

If the count is **1**, the tool asks for labels 2 to 6. If it is **more than 1**, only the count is recorded (multi-task notes are used only to score splitting).

## Label 2: task type

- **`email`**: the task is to **send, email, mail, write to, reply to, forward or share a document with** a named person or group. A thank-you note or a reply to a message is `email`.
- **`normal`**: everything else, including *asking* someone for something, calls, meetings, reviews, coding, and reminding someone.

"Ask X for Y" is `normal` even though it could be done by email. Task type and dependency are independent: a note can be `email` and also a dependency.

## Label 3: dependency

**D1.** `has_dependency = yes` when completing the task needs action from **another internal team** (direction `need_from_them`), or another team is **waiting on or needs** something from the person (direction `they_need_from_me`). It may be stated or clearly implied.

- **D2.** "Another team" is any directory team other than **product**. People belong to their team, so Ravi means *data*, Meera means *payments*, Karan means *platform*. People in **product** (Jiya, Aarav) never create a dependency.
- **D3.** Needing **access, permission, approval, credentials, a fix, data or a change** in a system owned by another team counts as a dependency on its owner, even if no team is named. The owner is whoever the directory says owns that system.
- **D4.** Merely mentioning a team, person or system is **not** a dependency: reading a team's docs, working in their system, or sending someone a deliverable ("send the report to Ravi"). Sending something only counts as `they_need_from_me` if the note says they need it, are waiting for it or are blocked on it.
- **D5.** External parties (vendors, banks, customers, clients) are not teams: **no dependency**.
- **D6.** An internal group that is not in the directory (legal, finance, HR): `has_dependency = yes`, team `unknown`.
- **D7.** If a note contains a dependency in both directions, flag it.

When `has_dependency = yes`, record the **direction** and the **team id** (`product`, `payments`, `platform`, `data`, or `unknown`). When no, both are `none`.

## Label 4: deadline

The deadline is the time by which the **task** must be done, as ISO 8601 in IST (the tool builds it from `YYYY-MM-DD HH:MM`). No deadline is `null`. Every deadline is after the reference point.

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
| a date without a year ("5 nov") | 2026 |
| an event time used as the cutoff ("before the 3pm standup") | that event's time, 15:00 today |
| "urgent", "asap", "important", "soon", or no time words at all | **`null`** (urgency is not a deadline) |

If a time phrase is not in this table, **flag the note**.

## Label 5: needs clarification

`yes` if **any** of these holds, otherwise `no`:

- **C1.** The action or object is vague, so you cannot tell what the person should actually do ("follow up on that", "sort out the permissions issue").
- **C2.** A reference cannot be resolved ("them", "him", "that thing", "the demo" with no date or identity given).
- **C3.** There is a dependency but the team is `unknown`.
- **C4.** A time phrase cannot be turned into a date with the table ("before it expires", "after the review").

Missing priority, a missing reviewer or a missing deadline on an otherwise clear task is **not** a reason to say `yes`.

## Using the tool

```powershell
python -m eval.label_tool dev        # practice on the 15 dev notes first
python -m eval.label_tool test       # the 60 test notes
python -m eval.label_tool test --stats
```

Answer every prompt explicitly (there are no defaults). `?` shows the short rules, `f` flags a note, `s` skips it for now, `q` quits and keeps your progress. Labels are saved to `eval/labels/` with a timestamp the moment you confirm each note.

## Provenance, stated plainly

- The **notes** were written by Claude (an AI assistant), not by the models being evaluated. They were written after the prompts were fixed, and the prompts will not be tuned on the test notes.
- **No labels** were written, drafted or suggested by any AI. The label files are produced only by `label_tool.py` from your answers.
- The assistant will not read the test labels before the prompts are frozen.
