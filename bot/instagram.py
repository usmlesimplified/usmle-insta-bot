"""Instagram API with Instagram Login (graph.instagram.com): carousel + story publishing, token refresh."""

import logging
import time

import requests

log = logging.getLogger(__name__)
HOST = "https://graph.instagram.com"


class IGError(RuntimeError):
    pass


class Instagram:
    def __init__(self, token: str, user_id: str | None = None, version: str = "v26.0",
                 session: requests.Session | None = None, poll_interval: float = 5.0):
        if not token:
            raise IGError("IG_ACCESS_TOKEN is not set")
        self.token = token
        self.version = version
        self.s = session or requests.Session()
        self.poll_interval = poll_interval
        self._user_id = user_id

    # ---- low level
    def _url(self, path: str) -> str:
        return f"{HOST}/{self.version}/{path.lstrip('/')}"

    def _call(self, method: str, path: str, *, params=None, json=None, retries: int = 3):
        headers = {"Authorization": f"Bearer {self.token}"}
        for attempt in range(retries + 1):
            try:
                r = self.s.request(method, self._url(path), params=params, json=json, headers=headers, timeout=60)
            except requests.RequestException as e:
                if attempt < retries:
                    time.sleep(3 * (attempt + 1))
                    continue
                raise IGError(f"network error calling {path}: {e}") from None
            if r.status_code >= 500 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                data = {"raw": r.text[:300]}
            if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
                err = data.get("error", data) if isinstance(data, dict) else data
                raise IGError(f"{method} {path} failed ({r.status_code}): {err}")
            return data
        raise IGError(f"{method} {path} failed after retries")

    # ---- account
    @property
    def user_id(self) -> str:
        if not self._user_id:
            me = self._call("GET", "me", params={"fields": "user_id,username"})
            self._user_id = str(me.get("user_id") or me.get("id"))
            log.info("Publishing as @%s (%s)", me.get("username"), self._user_id)
        return self._user_id

    def publishing_quota(self) -> dict:
        data = self._call("GET", f"{self.user_id}/content_publishing_limit", params={"fields": "quota_usage,config"})
        return (data.get("data") or [{}])[0]

    # ---- containers
    def create_image(self, image_url: str, *, carousel_item=False, alt_text: str | None = None) -> str:
        body = {"image_url": image_url}
        if carousel_item:
            body["is_carousel_item"] = True
        if alt_text:
            body["alt_text"] = alt_text[:1000]
        return self._call("POST", f"{self.user_id}/media", json=body)["id"]

    def create_carousel(self, children: list[str], caption: str, ai_label: bool = False) -> str:
        body = {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
        if ai_label:
            body["is_ai_generated"] = True
        return self._call("POST", f"{self.user_id}/media", json=body)["id"]

    def create_story(self, image_url: str) -> str:
        return self._call("POST", f"{self.user_id}/media",
                          json={"image_url": image_url, "media_type": "STORIES"})["id"]

    def container_status(self, container_id: str) -> str:
        return self._call("GET", container_id, params={"fields": "status_code,status"}).get("status_code", "")

    def wait_ready(self, container_id: str, timeout: int = 300):
        deadline = time.time() + timeout
        while True:
            status = self.container_status(container_id)
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                detail = self._call("GET", container_id, params={"fields": "status"}).get("status")
                raise IGError(f"container {container_id} is {status}: {detail}")
            if time.time() > deadline:
                raise IGError(f"container {container_id} not ready after {timeout}s (status {status})")
            time.sleep(self.poll_interval)

    def publish(self, container_id: str) -> str:
        try:
            return self._call("POST", f"{self.user_id}/media_publish", json={"creation_id": container_id},
                              retries=0)["id"]
        except IGError:
            # Never blindly retry a publish (it could post twice). Check whether it actually went out.
            time.sleep(self.poll_interval)
            if self.container_status(container_id) == "PUBLISHED":
                latest = self._call("GET", f"{self.user_id}/media", params={"fields": "id", "limit": 1})
                items = latest.get("data") or []
                if items:
                    return items[0]["id"]
            time.sleep(10)
            return self._call("POST", f"{self.user_id}/media_publish", json={"creation_id": container_id},
                              retries=0)["id"]

    def permalink(self, media_id: str) -> str | None:
        try:
            return self._call("GET", media_id, params={"fields": "permalink"}).get("permalink")
        except IGError:
            return None

    # ---- high level
    def publish_carousel(self, image_urls: list[str], alt_texts: list[str], caption: str,
                         ai_label: bool = False) -> tuple[str, str | None]:
        if not 2 <= len(image_urls) <= 10:
            raise IGError("a carousel needs 2-10 images")
        children = []
        for url, alt in zip(image_urls, alt_texts):
            cid = self.create_image(url, carousel_item=True, alt_text=alt)
            children.append(cid)
        for cid in children:
            self.wait_ready(cid)
        parent = self.create_carousel(children, caption, ai_label=ai_label)
        self.wait_ready(parent)
        media_id = self.publish(parent)
        return media_id, self.permalink(media_id)

    def publish_story(self, image_url: str) -> str:
        cid = self.create_story(image_url)
        self.wait_ready(cid)
        return self.publish(cid)


def refresh_long_lived_token(token: str, session: requests.Session | None = None) -> dict:
    """Extend a long-lived Instagram token by another 60 days (token must be >24h old)."""
    s = session or requests.Session()
    r = s.get(f"{HOST}/refresh_access_token",
              params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=60)
    data = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in data:
        raise IGError(f"token refresh failed ({r.status_code}): {data.get('error', data)}")
    return data
