"""Refresh the long-lived Instagram token and write the new one to a file (never to the log).

Used by .github/workflows/refresh-token.yml, which then stores it back into the repo secret.
"""

import os
import sys

from .instagram import refresh_long_lived_token


def main():
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        sys.exit("IG_ACCESS_TOKEN is not set")
    out_path = sys.argv[1] if len(sys.argv) > 1 else "new_token.txt"
    data = refresh_long_lived_token(token)
    with open(out_path, "w") as f:
        f.write(data["access_token"])
    days = int(data.get("expires_in", 0)) // 86400
    print(f"Token refreshed; valid for about {days} more days.")


if __name__ == "__main__":
    main()
