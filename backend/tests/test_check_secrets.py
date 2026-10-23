import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_secrets.py"
PREFIX = "gsk" + "_"  # built at runtime so this test file never contains the literal


def run_in_repo(tmp_path, files: dict[str, str], flag: str):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git("add", "-f", ".")
    return subprocess.run(
        [sys.executable, str(SCRIPT), flag], cwd=tmp_path, capture_output=True, text=True
    )


def test_clean_repo_passes(tmp_path):
    result = run_in_repo(tmp_path, {"a.py": "print('hi')\n"}, "--staged")
    assert result.returncode == 0


def test_key_prefix_is_blocked_and_not_echoed(tmp_path):
    fake = PREFIX + "abcdefghijklmnopqrstuvwxyz0123456789"
    result = run_in_repo(tmp_path, {"config.py": f"KEY = '{fake}'\n"}, "--staged")
    assert result.returncode == 1
    assert "config.py:1" in result.stderr
    assert fake not in result.stderr + result.stdout


def test_all_mode_also_scans_tracked_files(tmp_path):
    result = run_in_repo(tmp_path, {"docs/notes.md": f"oops {PREFIX}x\n"}, "--all")
    assert result.returncode == 1


def test_secret_files_are_blocked_even_when_empty(tmp_path):
    assert run_in_repo(tmp_path, {".env": "X=1\n"}, "--staged").returncode == 1


def test_secrets_folder_is_blocked(tmp_path):
    result = run_in_repo(tmp_path, {"secrets/gmail_credentials.json": "{}"}, "--staged")
    assert result.returncode == 1


def test_env_example_is_allowed(tmp_path):
    assert run_in_repo(tmp_path, {".env.example": "GROQ_API_KEY=\n"}, "--staged").returncode == 0
