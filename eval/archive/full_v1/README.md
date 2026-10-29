# Archived: the first, larger evaluation design (superseded)

On 2026-10-30 the evaluation was re-scoped to a **lean** design (10 dev notes, 30 single-task test notes, three gold fields). Everything here belonged to the earlier design and is kept for the record only. **None of it is used by the current code, and none of it is a result.**

What this was: 15 dev and 60 test notes (including multi-task notes), with five gold fields per note (task type, dependency and its direction and team, deadline, needs clarification, and a task count), written by hand by Jiya with the blind labeling tool under `LABELING_GUIDE_v1.md`.

Why it is archived and not reused: the notes, the label scheme and the guide changed, so the labels no longer match the code that scores them. No model was ever run on the 60 test notes. A model was run on the 15 dev notes for validating the runner, and those outputs were not used to tune anything.

The labels stay human-written and are untouched. They are not reused for the lean sets, which have fresh notes and fresh blind labels.
