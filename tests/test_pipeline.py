"""Offline tests: no network, no API keys. Run with:  python -m pytest -q"""

import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = json.loads((ROOT / "samples" / "sample_post.json").read_text(encoding="utf-8"))


@pytest.fixture
def cfg():
    from bot.config import load_config
    return load_config()


# ---------------------------------------------------------------- topics

def test_theme_rotation_covers_week():
    from bot.topics import theme_for
    monday = date(2026, 10, 26)
    series = [theme_for(monday + timedelta(days=i)).series for i in range(7)]
    assert series[0] == "Micro Monday" and series[1] == "Pharm Tuesday" and series[6] == "Sunday Vignette"
    assert theme_for(monday + timedelta(days=5)).formats == ("mnemonic",)


def test_candidates_respect_cooldown_and_are_deterministic():
    from bot.topics import select_candidates, theme_for
    day = date(2026, 10, 27)
    theme = theme_for(day)
    first = select_candidates({"posts": []}, theme, day, k=8)
    assert len(first) == 8 and len({c["id"] for c in first}) == 8
    assert all(c["discipline"] == "Pharmacology" for c in first)
    assert [c["id"] for c in first] == [c["id"] for c in select_candidates({"posts": []}, theme, day, k=8)]
    hist = {"posts": [{"date": (day - timedelta(days=3)).isoformat(), "topic_id": c["id"]} for c in first]}
    second = select_candidates(hist, theme, day, k=8)
    assert not {c["id"] for c in first} & {c["id"] for c in second}


def test_posted_on_detects_same_day():
    from bot.topics import posted_on
    h = {"posts": [{"date": "2026-10-27", "media_id": "1"}]}
    assert posted_on(h, date(2026, 10, 27))
    assert not posted_on(h, date(2026, 10, 28))


# ---------------------------------------------------------------- llm helpers

@pytest.mark.parametrize("text", [
    'Here you go:\n```json\n{"a": 1, "b": [1,2]}\n```',
    'Sure. {"a": 1, "b": [1,2]} hope that helps',
    'notes {bad} then ```\n{"a": 1, "b": [1,2]}\n```',
])
def test_extract_json(text):
    from bot.llm import extract_json
    assert extract_json(text) == {"a": 1, "b": [1, 2]}


# ---------------------------------------------------------------- schema / caption

def test_sample_post_is_valid():
    from bot.schema import validate_post
    assert validate_post(SAMPLE) == []


def test_validator_catches_problems():
    from bot.schema import validate_post
    bad = copy.deepcopy(SAMPLE)
    bad["slides"][0]["headline"] = "x" * 200
    bad["slides"].pop(6)  # remove answer slide after question
    bad["hashtags"] = ["#a"]
    errs = validate_post(bad)
    assert any("headline" in e for e in errs)
    assert any("answer" in e for e in errs)
    assert any("hashtags" in e for e in errs)


def test_caption_has_exactly_five_hashtags_and_fits(cfg):
    from bot.caption import build_caption
    post = copy.deepcopy(SAMPLE)
    post["caption"]["body"].append("Remember #microbiology and #pathology too")
    post["hashtags"] = ["#Pharmacology", "usmle", "#Anti-biotics", "#medstudent", "#extra"]
    cap = build_caption(post, cfg)
    tags = [w for w in cap.split() if w.startswith("#")]
    assert len(tags) == 5, tags
    assert tags[:2] == ["#usmlestep1", "#usmle"]
    assert "#antibiotics" in tags
    assert len(cap) <= 2200
    assert cap.startswith("Protein synthesis inhibitors for USMLE Step 1")


def test_alt_text_limits():
    from bot.caption import alt_text_for
    for i, s in enumerate(SAMPLE["slides"], 1):
        a = alt_text_for(s, i, len(SAMPLE["slides"]), SAMPLE["title"])
        assert 20 < len(a) <= 1000 and "**" not in a


# ---------------------------------------------------------------- rendering

def test_render_sample(tmp_path, cfg):
    from bot.render import SlideRenderer, H, W
    r = SlideRenderer(cfg)
    paths, overflow = r.render_post(SAMPLE, tmp_path, "Pharmacology")
    assert overflow == []
    assert len(paths) == len(SAMPLE["slides"])
    for p in paths:
        im = Image.open(p)
        assert im.format == "JPEG" and im.size == (W, H) and im.mode == "RGB"
    story = r.render_story(SAMPLE, tmp_path / "story.jpg", "Pharm Tuesday")
    assert Image.open(story).size == (1080, 1920)


def test_render_flags_unreadably_dense_slides(tmp_path, cfg):
    from bot.render import SlideRenderer
    dense = copy.deepcopy(SAMPLE)
    dense["slides"][2]["rows"] = [["Thrombocytopenia " * 4] * 3 for _ in range(6)]
    _, overflow = SlideRenderer(cfg).render_post(dense, tmp_path, "Pharmacology")
    assert 3 in overflow


# ---------------------------------------------------------------- Instagram client (fake HTTP)

class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.content = json.dumps(data).encode()
        self.text = json.dumps(data)
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._data


class FakeGraph:
    """Simulates graph.instagram.com closely enough to exercise the publish flow."""

    def __init__(self, fail_first_publish=False):
        self.calls = []
        self.n = 0
        self.containers = {}
        self.fail_first_publish = fail_first_publish
        self.published = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer TOKEN"
        path = url.split("/v26.0/")[1]
        self.calls.append((method, path, json or params))
        if method == "POST" and path.endswith("/media"):
            self.n += 1
            cid = f"c{self.n}"
            self.containers[cid] = "FINISHED"
            return FakeResp(200, {"id": cid})
        if method == "POST" and path.endswith("/media_publish"):
            cid = json["creation_id"]
            self.containers[cid] = "PUBLISHED"
            self.published.append(cid)
            if self.fail_first_publish and len(self.published) == 1:
                return FakeResp(500, {"error": {"message": "timeout"}})
            return FakeResp(200, {"id": f"m-{cid}"})
        if method == "GET" and path.endswith("/media"):
            return FakeResp(200, {"data": [{"id": f"m-{self.published[-1]}"}]})
        if method == "GET" and path.endswith("content_publishing_limit"):
            return FakeResp(200, {"data": [{"quota_usage": 0}]})
        if method == "GET" and path == "me":
            return FakeResp(200, {"user_id": "999", "username": "usmle.simplified"})
        if method == "GET" and (params or {}).get("fields", "").startswith("status"):
            return FakeResp(200, {"status_code": self.containers[path]})
        if method == "GET" and (params or {}).get("fields") == "permalink":
            return FakeResp(200, {"permalink": f"https://www.instagram.com/p/{path}/"})
        raise AssertionError(f"unexpected call {method} {path}")


def test_instagram_carousel_flow():
    from bot.instagram import Instagram
    g = FakeGraph()
    ig = Instagram("TOKEN", None, "v26.0", session=g, poll_interval=0)
    media, link = ig.publish_carousel([f"https://x/{i}.jpg" for i in range(3)], ["alt"] * 3, "caption")
    assert media == "m-c4" and link.endswith("/m-c4/")
    creates = [c for c in g.calls if c[0] == "POST" and c[1].endswith("/media")]
    assert all(c[2]["is_carousel_item"] and c[2]["alt_text"] == "alt" for c in creates[:3])
    assert creates[3][2]["media_type"] == "CAROUSEL" and creates[3][2]["children"] == "c1,c2,c3"
    assert g.published == ["c4"]  # published exactly once


def test_publish_is_not_duplicated_when_response_is_lost():
    from bot.instagram import Instagram
    g = FakeGraph(fail_first_publish=True)
    ig = Instagram("TOKEN", "999", "v26.0", session=g, poll_interval=0)
    media, _ = ig.publish_carousel(["https://x/1.jpg", "https://x/2.jpg"], ["a", "b"], "cap")
    assert g.published == ["c3"] and media == "m-c3"


# ---------------------------------------------------------------- full pipeline (fake Claude, IG, hosting)

class FakeClaude:
    def __init__(self, *a, **k):
        self.usage = {}

    def ask_json(self, system, prompt, **kw):
        if "Choose ONE topic" in prompt or "fixed by the account owner" in prompt:
            return {"chosen_id": "pharmacology-protein-synthesis-inhibitors-30s-vs-50s",
                    "topic": "Protein synthesis inhibitors", "discipline": "Pharmacology",
                    "why_today": "test", "format": "mnemonic", "angle": "", "key_facts": [], "common_traps": [],
                    "mnemonic_ideas": [], "seo_keywords": []}, [{"url": "https://example.org", "title": "x"}]
        if "Write today's Instagram carousel" in prompt:
            return copy.deepcopy(SAMPLE), []
        if "Fact-check this Instagram carousel" in prompt:
            return {"verdict": "pass", "issues": [], "corrected_post": None}, []
        raise AssertionError("unexpected prompt")


def test_end_to_end_publishes_once_and_records_history(tmp_path, monkeypatch):
    import bot.hosting
    import bot.instagram
    import bot.llm
    import bot.main as main

    published = []

    class FakeHost:
        def __init__(self, *a, **k):
            pass

        def upload_all(self, files, folder):
            return [f"https://raw.example/{folder}/{Path(f).name}" for f in files]

    class FakeIG:
        def __init__(self, *a, **k):
            pass

        def publishing_quota(self):
            return {}

        def publish_carousel(self, urls, alts, caption, ai_label=False):
            assert len(urls) == len(SAMPLE["slides"]) and len(alts) == len(urls)
            assert caption.count("#") == 5
            published.append("carousel")
            return "MEDIA1", "https://www.instagram.com/p/abc/"

        def publish_story(self, url):
            assert url.endswith("story.jpg")
            published.append("story")
            return "STORY1"

    monkeypatch.setattr(bot.llm, "make_llm", lambda cfg: FakeClaude())
    monkeypatch.setattr(bot.hosting, "GitHubImageHost", FakeHost)
    monkeypatch.setattr(bot.instagram, "Instagram", FakeIG)
    monkeypatch.setattr(main, "HISTORY", tmp_path / "history.json")
    monkeypatch.setattr(main, "OUT_DIR", tmp_path / "out")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    assert main.run(["--date", "2026-10-27"]) == 0
    hist = json.loads((tmp_path / "history.json").read_text())
    assert published == ["carousel", "story"]
    assert hist["posts"][0]["media_id"] == "MEDIA1" and hist["posts"][0]["story_id"] == "STORY1"
    assert (tmp_path / "out" / "2026-10-27" / "caption.txt").exists()

    # Second run the same day must not post again.
    assert main.run(["--date", "2026-10-27"]) == 0
    assert published == ["carousel", "story"]


# ---------------------------------------------------------------- GitHub image hosting (fake HTTP)

class FakeGitHub:
    def __init__(self, private=False, branch_exists=True):
        self.private, self.branch_exists = private, branch_exists
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, json=None, **kw):
        self.calls.append((method, url, json))
        if url.startswith("https://raw.githubusercontent.com/"):
            r = FakeResp(200, {})
            r.headers = {"Content-Type": "image/jpeg"}
            return r
        path = url.replace("https://api.github.com", "")
        if method == "GET" and path == "/repos/me/bot":
            return FakeResp(200, {"private": self.private, "default_branch": "main"})
        if method == "POST" and path.endswith("/git/blobs"):
            return FakeResp(201, {"sha": f"blob{len(self.calls)}"})
        if method == "POST" and path.endswith("/git/trees"):
            assert "base_tree" not in json  # fresh tree: only today's files
            return FakeResp(201, {"sha": "tree1"})
        if method == "POST" and path.endswith("/git/commits"):
            assert json["parents"] == []    # orphan commit: history does not grow
            return FakeResp(201, {"sha": "commit1"})
        if method == "GET" and path.endswith("/git/refs/heads/media"):
            return FakeResp(200 if self.branch_exists else 404, {})
        if method == "PATCH" and path.endswith("/git/refs/heads/media"):
            assert json == {"sha": "commit1", "force": True}
            return FakeResp(200, {})
        if method == "POST" and path.endswith("/git/refs"):
            assert json["ref"] == "refs/heads/media"
            return FakeResp(201, {})
        raise AssertionError(f"unexpected {method} {path}")


@pytest.mark.parametrize("branch_exists", [True, False])
def test_hosting_replaces_media_branch(tmp_path, branch_exists):
    from bot.hosting import GitHubImageHost
    files = []
    for i in range(2):
        p = tmp_path / f"slide_0{i + 1}.jpg"
        Image.new("RGB", (10, 10)).save(p)
        files.append(p)
    gh = FakeGitHub(branch_exists=branch_exists)
    urls = GitHubImageHost("me/bot", "T", session=gh).upload_all(files, "posts/2026-10-27")
    assert urls == [f"https://raw.githubusercontent.com/me/bot/media/posts/2026-10-27/slide_0{i}.jpg" for i in (1, 2)]


def test_hosting_refuses_private_repo(tmp_path):
    from bot.hosting import GitHubImageHost, HostingError
    with pytest.raises(HostingError, match="private"):
        GitHubImageHost("me/bot", "T", session=FakeGitHub(private=True)).upload_all([], "x")


# ---------------------------------------------------------------- Claude Code (subscription) backend

class FakeProc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_claude_code_backend_builds_safe_command_and_parses_json():
    from bot.llm import ClaudeCode
    seen = {}

    def runner(cmd, input, capture_output, text, timeout, env, cwd):
        seen.update(cmd=cmd, input=input, env=env, cwd=cwd)
        result = 'Done.\n```json\n{"topic": "X", "sources": [{"url": "https://a.org", "title": "A"}]}\n```'
        return FakeProc(json.dumps({"type": "result", "is_error": False, "result": result,
                                    "num_turns": 4, "total_cost_usd": 0.05}))

    cc = ClaudeCode("OAUTH", "sonnet", runner=runner)
    data, sources = cc.ask_json("sys", "pick a topic", web_search=True, max_searches=3)
    assert data["topic"] == "X" and sources == [{"url": "https://a.org", "title": "A"}]
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and "--bare" not in cmd  # bare mode would ignore the OAuth token
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--system-prompt") + 1] == "sys"
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "OAUTH" and "ANTHROPIC_API_KEY" not in seen["env"]
    assert "/usmle-insta-bot" not in seen["cwd"]  # never runs inside the repo
    assert '"sources"' in seen["input"]


def test_claude_code_backend_no_tools_and_json_repair():
    from bot.llm import ClaudeCode
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        res = "not json at all" if len(calls) == 1 else '{"ok": true}'
        return FakeProc(json.dumps({"is_error": False, "result": res}))

    data, _ = ClaudeCode("T", runner=runner).ask_json("s", "write", web_search=False)
    assert data == {"ok": True} and len(calls) == 2
    assert calls[0][calls[0].index("--tools") + 1] == "" and "--allowedTools" not in calls[0]


def test_claude_code_backend_stops_on_usage_limit():
    from bot.llm import ClaudeCode, LLMError
    calls = []

    def runner(cmd, **kw):
        calls.append(1)
        return FakeProc(json.dumps({"is_error": True, "subtype": "error", "result": "Claude usage limit reached"}), 1)

    with pytest.raises(LLMError, match="usage limit"):
        ClaudeCode("T", runner=runner).ask_json("s", "p")
    assert len(calls) == 1


def test_make_llm_prefers_subscription(monkeypatch):
    import bot.llm as L
    from bot.config import load_config
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setattr(L.shutil, "which", lambda b: "/usr/bin/claude")
    assert isinstance(L.make_llm(load_config()), L.ClaudeCode)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(L.LLMError):
        L.make_llm(load_config())
