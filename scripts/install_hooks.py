"""Enable the repo's git hooks: python -m scripts.install_hooks (run once per clone)."""

import subprocess

subprocess.run(["git", "config", "core.hooksPath", "scripts/hooks"], check=True)
print("Hooks enabled: commits containing a Groq key prefix or secret files are blocked.")
