# Worked examples for calibration

These are **practice notes that are NOT in the dev or test sets**, labeled by applying the approved guide ([LABELING_GUIDE.md](LABELING_GUIDE.md), version 2) so you can see how each rule is used. They are teaching material, not data: nothing here is scored. Read them, then re-check your own labels with `--review`.

Reference time for every note: **Monday 2 Nov 2026, 09:00 India time.** Your own team is **product**.

| Note | Type | Dependency | Deadline you enter | Why |
|---|---|---|---|---|
| send karan the architecture diagram by friday | `e` | none | `2026-11-06 23:59` | "send ... to a person" is email. Sending a deliverable is not a dependency (D4). A weekday alone is 23:59. |
| ask meera to approve the invoice template tomorrow morning | `n` | `1` need from them, **payments** | `2026-11-03 12:00` | "Ask X for Y" is normal. Needing an approval from a payments person is a dependency (D1, D3). "morning" is 12:00. |
| i need the customer data warehouse schema for my analysis | `n` | `1` need from them, **data** | (Enter, blank) | Needing information from a system another team owns counts (D3), even though no team is named. No time words, so no deadline. |
| anita is blocked on my funnel definitions, i owe them by 4 | `n` | `2` they need from you, **data** | `2026-11-02 16:00` | Anita is on data, and she is waiting on the writer (reverse dependency). A bare "4" is 4 PM (hours 1 to 6 are PM). |
| reply to the vendor's quote by EOD | `e` | none | `2026-11-02 18:00` | "reply to" is email. A vendor is not a team (D5). EOD is 18:00. |
| refactor the search page, it's urgent | `n` | none | (Enter, blank) | Urgency is not a deadline. |
| meet aarav about the sprint plan tomorrow | `n` | none | `2026-11-03 23:59` | A meeting is normal. Aarav is on product, your own team, so no dependency (D2). "tomorrow" alone is 23:59. |
| remind sneha about the staging reset tonight | `n` | `1` need from them, **platform** | `2026-11-02 21:00` | "remind someone" is normal, not email. The reset needs platform to act (D1). "tonight" is 21:00. |
| read the weekly report pipeline docs | `n` | none | (Enter, blank) | Reading another team's documentation only mentions a system (D4). |
| send the status update to arjun in 2 hours | `e` | none | `2026-11-02 11:00` | "send ... to a person" is email, and "in 2 hours" is 09:00 plus 2 hours. |
| write to the team about the thing before the offsite | **flag it (`f`)** | | | "before the offsite" is a time phrase that is not in the deadline table, and "the thing" is unclear. When a rule does not fit, flag instead of guessing. |

## The three slips to avoid

1. **Type:** `e` whenever the task is to *send, email, mail, write to, reply to or forward* something to a person or group. `n` only if it is anything else.
2. **Dependency:** ask "does this need another team to act, or is another team waiting on me?" Wanting access, data, information, an approval or a fix from a system another team owns counts. Just mentioning a team, person or system, sending a deliverable, vendors and banks, or your own team do not.
3. **Deadline:** any note with a time or day word gets a date and time from the table. A day alone is `23:59`, not 09:00 or 10:00, and "by 5" is `17:00`, not blank. Only urgency words or no time words mean blank.
