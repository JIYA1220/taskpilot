# Rewritten notes

When the labeler flags a note as ambiguous, the labeling guide says the note is rewritten and the new version is labeled. This file records each rewrite so the history is honest. It records only that a note changed and why it was flagged; it never contains a label.

| Date | Note id | Set | Original text | New text | Flagged because (the labeler's words) |
|---|---|---|---|---|---|
| 2026-10-27 | `t046` | test | `send it to him asap` | `clean up the old stuff` | "here it says send it him....to whom ?" |

The old flag stays in `eval/labels/dataset.labels.jsonl` as history. The new text needs a fresh label (`python -m eval.label_tool test --relabel t046`).
