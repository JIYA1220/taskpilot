"""Print a random JWT secret to paste into .env: python -m scripts.gen_secret"""

import secrets

print(secrets.token_urlsafe(48))
