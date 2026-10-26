"""Blind labeling tool.  python -m eval.label_tool dev|test [--stats] [--relabel ID]

Shows only the note text. It never calls a model, never imports one, and never shows
the design metadata, so the labels are entirely yours. Each confirmed label is saved
at once (append-only, timestamped) and you can quit and resume at any time.
"""

import argparse
import json
import sys
from collections.abc import Callable

from eval import labels as store

DIRECTORY = json.loads((store.EVAL_DIR / "directory.json").read_text(encoding="utf-8"))
TEAM_IDS = [t["id"] for t in DIRECTORY["teams"]]

SHORT_RULES = """
SHORT RULES (full guide: eval/LABELING_GUIDE.md)
 Reference time: Mon 2 Nov 2026 09:00 IST. Your own team: product.
 Tasks     one per distinct action/object. Prepare+send the same thing = 1. Text aimed at the AI is not a task.
 Type      email = send/email/mail/write to/reply/forward to a person. Everything else (incl. "ask X for Y") = normal.
 Dependency yes = needs another team's action, or another team needs something from you (stated or clearly implied).
           Needing access/approval/fix/data in another team's system counts. Just mentioning a team/person/system does not.
           Vendors/banks/customers = no. Internal group not in directory (legal, finance) = yes, team unknown.
 Deadline  clock time -> that time (tomorrow if already past) | bare 'by 5' 1-6=PM, 7-11=AM | EOD=18:00 | tonight=21:00
           morning=12:00 | date/weekday/today/tomorrow alone=23:59 | 'in N hours'=09:00+N | urgent/asap/none = blank
 Clarify   yes if: vague action; unresolved reference (them/that); dependency team unknown; unresolvable time phrase.
           A missing deadline/priority/reviewer on a clear task is NOT a reason.
 Keys      ? rules   f flag as ambiguous   s skip for now   q quit (progress is saved)
""".strip()


class Quit(Exception):
    pass


class Skip(Exception):
    pass


class Flag(Exception):
    pass


Input = Callable[[str], str]
Output = Callable[[str], None]


def read(prompt: str, inp: Input, out: Output, *, commands: bool = True) -> str:
    while True:
        answer = inp(prompt).strip()
        key = answer.lower()
        if commands and key == "?":
            out(SHORT_RULES)
        elif commands and key == "q":
            raise Quit
        elif commands and key == "s":
            raise Skip
        elif commands and key == "f":
            raise Flag
        else:
            return answer


def choose(prompt: str, options: dict, inp: Input, out: Output):
    while True:
        answer = read(prompt, inp, out).lower()
        if answer in options:
            return options[answer]
        out(f"  please answer one of: {', '.join(options)}")


def ask_count(inp: Input, out: Output) -> int:
    while True:
        answer = read("1. How many tasks are in this note? ", inp, out)
        if answer.isdigit() and 1 <= int(answer) <= 10:
            return int(answer)
        out("  enter a whole number from 1 to 10")


def ask_deadline(inp: Input, out: Output) -> str | None:
    while True:
        answer = read("5. Deadline as YYYY-MM-DD HH:MM in IST, or Enter for none: ", inp, out)
        try:
            return store.parse_deadline(answer)
        except ValueError as exc:
            out(f"  {exc}")


def ask_labels(inp: Input, out: Output) -> dict:
    n = ask_count(inp, out)
    if n > 1:
        return {"n_tasks": n}
    task_type = choose("2. Task type: [n]ormal or [e]mail? ", {"n": "normal", "e": "email"}, inp, out)
    has_dep = choose("3. Dependency on another team (either direction)? [y]es / [n]o: ", {"y": True, "n": False}, inp, out)
    direction, team = "none", "none"
    if has_dep:
        direction = choose(
            "   Direction: [1] you need something from them  [2] they need something from you: ",
            {"1": "need_from_them", "2": "they_need_from_me"},
            inp,
            out,
        )
        menu = {str(i + 1): t for i, t in enumerate([*TEAM_IDS, "unknown"])}
        listing = "  ".join(f"[{k}] {v}" for k, v in menu.items())
        team = choose(f"   Team: {listing}: ", menu, inp, out)
    deadline = ask_deadline(inp, out)
    needs = choose("6. Needs clarification? [y]es / [n]o: ", {"y": True, "n": False}, inp, out)
    return {
        "n_tasks": 1,
        "task_type": task_type,
        "has_dependency": has_dep,
        "dependency_direction": direction,
        "dependency_team": team,
        "deadline": deadline,
        "needs_clarification": needs,
    }


def summarize(labels: dict) -> str:
    if labels["n_tasks"] > 1:
        return f"  {labels['n_tasks']} tasks (count only)"
    dep = "no" if not labels["has_dependency"] else f"yes ({labels['dependency_direction']}, {labels['dependency_team']})"
    return (
        f"  1 task | type: {labels['task_type']} | dependency: {dep} | "
        f"deadline: {labels['deadline'] or 'none'} | needs clarification: {'yes' if labels['needs_clarification'] else 'no'}"
    )


def label_item(set_name: str, item: dict, position: str, inp: Input, out: Output) -> str:
    """Label one note. Returns 'labeled', 'flagged', 'skipped' (quit raises Quit)."""
    out(f"\n=== Note {position} ===")
    out("\n".join("  | " + line for line in item["input"].splitlines()))
    out("Reference time: Mon 02 Nov 2026 09:00 IST.   ? rules   f flag   s skip   q quit")
    while True:
        try:
            labels = ask_labels(inp, out)
            problems = store.validate_labels(labels, TEAM_IDS)
            if problems:
                out("  Those answers do not fit together: " + "; ".join(problems) + "\n  Let's redo this note.")
                continue
            out(summarize(labels))
            decision = read("Press Enter to save, or r to redo this note: ", inp, out).lower()
            if decision == "r":
                continue
            note = read("Optional note about this label (Enter for none): ", inp, out, commands=False)
            store.append_record(set_name, {"id": item["id"], "status": "labeled", **labels, "notes": note})
            return "labeled"
        except Skip:
            return "skipped"
        except Flag:
            why = read("Why is this note ambiguous? ", inp, out, commands=False)
            store.append_record(set_name, {"id": item["id"], "status": "flagged", "notes": why})
            return "flagged"


def show_stats(set_name: str, out: Output) -> None:
    p = store.progress(set_name)
    out(f"{set_name}: {p.labeled} labeled, {p.flagged} flagged, {p.remaining} remaining of {p.total}")


def describe(record: dict | None) -> str:
    if record is None:
        return "NOT LABELED"
    if record["status"] == "flagged":
        return f"FLAGGED ({record.get('notes') or 'no reason given'})"
    return summarize(record).strip()


def show_review(set_name: str, out: Output) -> None:
    """Your own labels next to each note, for you to scan and correct. Nothing leaves your terminal."""
    latest = store.latest_by_id(store.read_history(set_name))
    for item in store.load_inputs(set_name):
        out(f"{item['id']}  | {item['input'].replace(chr(10), ' / ')}")
        out(f"        -> {describe(latest.get(item['id']))}")
    out(f"\nTo change one: python -m eval.label_tool {set_name} --relabel <id>   (the old answer stays in the history)")


def show_summary(set_name: str, out: Output) -> None:
    """Counts of your answers. A pattern such as 'no deadline on any note' is worth a second look."""
    latest = store.latest_by_id(store.read_history(set_name))
    labeled = [r for r in latest.values() if r["status"] == "labeled"]
    single = [r for r in labeled if r["n_tasks"] == 1]
    out(f"{set_name}: {len(labeled)} labeled notes ({len(single)} single-task, {len(labeled) - len(single)} multi-task)")
    for name, values in (
        ("task type", [r["task_type"] for r in single]),
        ("has a dependency", ["yes" if r["has_dependency"] else "no" for r in single]),
        ("dependency direction", [r["dependency_direction"] for r in single]),
        ("dependency team", [r["dependency_team"] for r in single]),
        ("needs clarification", ["yes" if r["needs_clarification"] else "no" for r in single]),
    ):
        counts = {v: values.count(v) for v in sorted(set(values))}
        out(f"  {name}: " + ", ".join(f"{v} x{n}" for v, n in counts.items()))
    with_deadline = sum(1 for r in single if r["deadline"])
    out(f"  deadline: {with_deadline} with a deadline, {len(single) - with_deadline} with none")


def run(set_name: str, inp: Input = input, out: Output = print, relabel: str | None = None) -> None:
    items = store.load_inputs(set_name)
    latest = store.latest_by_id(store.read_history(set_name))
    if relabel:
        todo = [(i, it) for i, it in enumerate(items, 1) if it["id"] == relabel]
        if not todo:
            out(f"No note with id {relabel} in the {set_name} set.")
            return
    else:
        todo = [(i, it) for i, it in enumerate(items, 1) if it["id"] not in latest]
    if not todo:
        out("Nothing left to label.")
        show_stats(set_name, out)
        return
    try:
        for index, item in todo:
            label_item(set_name, item, f"{index} of {len(items)}  (id {item['id']})", inp, out)
    except (Quit, EOFError, KeyboardInterrupt):
        out("\nStopped. Everything you confirmed is saved.")
    show_stats(set_name, out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("set", choices=sorted(store.SETS))
    parser.add_argument("--stats", action="store_true", help="show progress and exit")
    parser.add_argument("--review", action="store_true", help="print each note with your current label, to check your work")
    parser.add_argument("--summary", action="store_true", help="print counts of your answers, to spot patterns")
    parser.add_argument("--relabel", metavar="ID", help="label one note again (the old label stays in the history)")
    args = parser.parse_args(argv)
    if args.review:
        show_review(args.set, print)
    elif args.summary:
        show_summary(args.set, print)
    elif args.stats:
        show_stats(args.set, print)
    else:
        run(args.set, relabel=args.relabel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
