"""Block Groq-style keys and secret files from entering git.

    python scripts/check_secrets.py --staged   (pre-commit hook: scans the index)
    python scripts/check_secrets.py --all      (CI: scans every tracked file)

Prints file:line only, never the matching text, so a leaked key is not echoed.
"""

import subprocess
import sys

NEEDLE = "gsk" + "_"  # split so this file does not match itself
FORBIDDEN_PATHS = (".env", "token.json")
FORBIDDEN_PREFIXES = ("secrets/",)


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def list_files(staged: bool) -> list[str]:
    if staged:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACM", "-z")
    else:
        out = git("ls-files", "-z")
    return [p for p in out.decode().split("\0") if p]


def read(path: str, staged: bool) -> bytes:
    if staged:
        return git("show", f":{path}")
    with open(path, "rb") as f:
        return f.read()


def scan(staged: bool) -> list[str]:
    problems = []
    for path in list_files(staged):
        name = path.rsplit("/", 1)[-1]
        if name in FORBIDDEN_PATHS or path.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"{path}: secret file must not be committed")
            continue
        try:
            text = read(path, staged).decode("utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary or deleted
        for number, line in enumerate(text.splitlines(), start=1):
            if NEEDLE in line:
                problems.append(f"{path}:{number}: contains a Groq key prefix")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("--staged", "--all"):
        print(__doc__)
        return 2
    problems = scan(staged=argv[0] == "--staged")
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print("Blocked. Remove the secret (and rotate it if it was ever pushed).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
