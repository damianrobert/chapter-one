"""Print a test login token for a user: `python -m app.dev_token <user-uuid>`.

Chapter One has no login page or user accounts yet, so this stands in for "logging
in" during development. It can only run where the secret is already available.
"""

import sys
from uuid import UUID

from app.auth import create_token
from app.config import settings

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.dev_token <user-uuid>")
    print(create_token(str(UUID(sys.argv[1])), settings.jwt_secret))
