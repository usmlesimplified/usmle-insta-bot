"""Read-only connection check: confirms the Instagram token works. Publishes nothing.

Usage: python -m bot.check_instagram
"""

import sys

from .config import load_config
from .instagram import IGError, Instagram


def main() -> int:
    cfg = load_config()
    try:
        ig = Instagram(cfg.ig_access_token, cfg.ig_user_id, cfg["graph_api_version"],
                       host=cfg.get("ig_api_host", "graph.facebook.com"), username=cfg["handle"])
        uid = ig.user_id
        info = ig._call("GET", uid, params={"fields": "username,followers_count,media_count"})
        msg = (f"OK: connected to @{info.get('username')} (id {uid}), "
               f"{info.get('followers_count', '?')} followers, {info.get('media_count', '?')} posts")
        print(msg)
        print(f"::notice title=Instagram check::{msg}")
        try:
            print(f"Publishing quota: {ig.publishing_quota()}")
        except IGError as e:
            print(f"(could not read publishing quota: {e})")
        return 0
    except IGError as e:
        print(f"FAILED: {e}")
        print(f"::error title=Instagram check::{str(e)[:500]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
