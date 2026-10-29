"""Hosts slide images at public URLs (Instagram's API only accepts image URLs, not uploads).

Images go to a separate `media` branch of this (public) GitHub repo and are served from
raw.githubusercontent.com. Each run replaces the branch with a single fresh commit holding only that day's
images, so the repository does not grow over time (Instagram keeps its own copy once a post is published).
"""

import base64
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)
API = "https://api.github.com"


class HostingError(RuntimeError):
    pass


class GitHubImageHost:
    def __init__(self, repo: str, token: str, branch: str = "media", session: requests.Session | None = None):
        if not repo or not token:
            raise HostingError("GITHUB_REPOSITORY and GITHUB_TOKEN must be set (they are automatic inside GitHub Actions)")
        self.repo, self.branch = repo, branch
        self.s = session or requests.Session()
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _req(self, method, path, ok=(200, 201), **kw):
        for attempt in range(4):
            r = self.s.request(method, f"{API}{path}", headers=self.headers, timeout=60, **kw)
            if r.status_code in ok:
                return r
            if r.status_code in (502, 503, 504) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise HostingError(f"GitHub {method} {path} failed: {r.status_code} {r.text[:200]}")

    def check_public(self):
        info = self._req("GET", f"/repos/{self.repo}").json()
        if info.get("private"):
            raise HostingError("the repository is private, so Instagram cannot download the images. "
                               "Make the repo public (Settings > General > Danger Zone > Change visibility).")
        return info

    def raw_url(self, remote_path: str) -> str:
        return f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{remote_path}"

    def publish_files(self, files: list[Path], folder: str) -> list[str]:
        """Replace the media branch with one commit containing exactly these files."""
        tree = []
        for f in files:
            content = base64.b64encode(Path(f).read_bytes()).decode()
            blob = self._req("POST", f"/repos/{self.repo}/git/blobs",
                             json={"content": content, "encoding": "base64"}).json()
            tree.append({"path": f"{folder}/{Path(f).name}", "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree_sha = self._req("POST", f"/repos/{self.repo}/git/trees", json={"tree": tree}).json()["sha"]
        commit_sha = self._req("POST", f"/repos/{self.repo}/git/commits",
                               json={"message": f"media for {folder}", "tree": tree_sha, "parents": []}).json()["sha"]
        ref = f"/repos/{self.repo}/git/refs/heads/{self.branch}"
        exists = self.s.request("GET", f"{API}{ref}", headers=self.headers, timeout=60).status_code == 200
        if exists:
            self._req("PATCH", ref, json={"sha": commit_sha, "force": True})
        else:
            self._req("POST", f"/repos/{self.repo}/git/refs",
                      json={"ref": f"refs/heads/{self.branch}", "sha": commit_sha})
        return [self.raw_url(t["path"]) for t in tree]

    def wait_until_public(self, url: str, timeout: int = 180):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                r = self.s.request("GET", url, timeout=30)
                last = r.status_code
                if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
                    return
            except requests.RequestException as e:
                last = str(e)
            time.sleep(5)
        raise HostingError(f"image never became publicly reachable: {url} (last: {last})")

    def upload_all(self, files: list[Path], folder: str) -> list[str]:
        self.check_public()
        urls = self.publish_files(files, folder)
        for u in urls:
            self.wait_until_public(u)
        return urls
