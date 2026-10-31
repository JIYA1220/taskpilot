# Archived: round 1 of the lean evaluation (human labels), INVALIDATED

On 2026-11-01 the lean evaluation was run once on 30 test notes whose gold labels Jiya had written by hand with the blind labeling tool. **That result is invalidated and must not be quoted.** It is kept, unaltered, for the record.

**Why it was invalidated.** After the run, the error analysis showed that most of the 13 notes marked wrong for the main model were notes whose gold label disagreed with the approved labeling guide (version 2), not mistakes by the models. The disagreements were of the same kinds in several notes: "send / forward / reply to a person" labeled `normal` instead of `email`; a dependency recorded on notes that only send a deliverable (rule D4) and missing on notes that ask for access or work from another team (rule D3); day-only deadlines entered as 09:00 to 10:30 instead of 23:59; and one deadline typed with the wrong month. Two earlier label reviews had already found the same patterns in the dev labels. So the scores measured the labels more than the models.

**What was kept:** `dataset.jsonl` (the round 1 test notes), `labels/dataset.labels.jsonl` (Jiya's labels, untouched and still human-written), `coverage.json` and `build_inputs.py` (how the set was generated). The matching results are in `eval/results/superseded/`.

**What replaced it:** round 2 uses 30 fresh notes (no model has seen them) whose gold labels were written by the AI assistant applying the guide, by the project owner's instruction. Those labels are **not** human-written and are marked as such everywhere. See the README.
