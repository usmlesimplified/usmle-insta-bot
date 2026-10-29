"""Thin wrapper around the Claude API: JSON answers, optional web search."""

import json
import logging
import re

log = logging.getLogger(__name__)

WEB_SEARCH_TOOL_TYPE = "web_search_20250305"


class LLMError(RuntimeError):
    pass


def extract_json(text: str):
    """Return the last JSON object in `text` (fenced ```json blocks preferred)."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    for block in reversed(fenced):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    best = None
    for m in re.finditer(r"\{", text):
        try:
            obj, end = decoder.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (best is None or end > best[1]):
            best = (obj, end)
    if best is None:
        raise ValueError("no JSON object found in model output")
    return best[0]


class Claude:
    def __init__(self, api_key: str, model: str):
        import anthropic  # imported lazily so offline tests don't need the SDK

        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=600)
        self.model = model
        self.usage = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}

    def _track(self, resp):
        u = getattr(resp, "usage", None)
        if not u:
            return
        self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        if stu is not None:
            self.usage["web_searches"] += getattr(stu, "web_search_requests", 0) or 0

    def _run(self, system: str, messages: list, tools: list | None, max_tokens: int):
        """Call the API, continuing through `pause_turn` stops from server tools."""
        texts, sources = [], []
        msgs = list(messages)
        for _ in range(6):
            kwargs = dict(model=self.model, max_tokens=max_tokens, system=system, messages=msgs)
            if tools:
                kwargs["tools"] = tools
            resp = self.client.messages.create(**kwargs)
            self._track(resp)
            for block in resp.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    texts.append(block.text)
                elif btype == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for r in content:
                            url = getattr(r, "url", None)
                            if url:
                                sources.append({"url": url, "title": getattr(r, "title", "")})
            if resp.stop_reason == "pause_turn":
                msgs = msgs + [{"role": "assistant", "content": resp.content}]
                continue
            if resp.stop_reason == "max_tokens":
                log.warning("Claude hit max_tokens; output may be truncated")
            return "".join(texts), sources, msgs + [{"role": "assistant", "content": resp.content}]
        raise LLMError("too many pause_turn continuations")

    def ask_json(self, system: str, prompt: str, *, web_search: bool = False,
                 max_searches: int = 5, max_tokens: int = 12000, retries: int = 2):
        tools = None
        if web_search:
            tools = [{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_searches}]
        messages = [{"role": "user", "content": prompt}]
        all_sources = []
        last_text = ""
        for attempt in range(retries + 1):
            text, sources, convo = self._run(system, messages, tools, max_tokens)
            all_sources.extend(sources)
            last_text = text
            try:
                data = extract_json(text)
                # de-duplicate sources, keep order
                seen, uniq = set(), []
                for s in all_sources:
                    if s["url"] not in seen:
                        seen.add(s["url"])
                        uniq.append(s)
                return data, uniq
            except ValueError:
                log.warning("No valid JSON in reply (attempt %d); asking again", attempt + 1)
                messages = convo + [{"role": "user", "content":
                                     "Your reply did not contain a valid JSON object. Do not search again. "
                                     "Reply with ONLY the complete JSON object in a ```json code block, nothing else."}]
        raise LLMError(f"model never returned valid JSON. Last output starts: {last_text[:300]!r}")


# ---------------------------------------------------------------------------------------------
# Claude Code backend: runs the `claude` CLI in print mode, authenticated with the owner's
# Claude subscription through CLAUDE_CODE_OAUTH_TOKEN (made once with `claude setup-token`).
# No API billing; usage counts against the subscription's limits.
# ---------------------------------------------------------------------------------------------

import os
import shutil
import subprocess
import tempfile
import time

SOURCES_NOTE = ('\n\nAlso include a top-level "sources" array in the JSON: [{"url": "...", "title": "..."}] '
                "listing the pages you actually used (max 10).")


class ClaudeCode:
    def __init__(self, oauth_token: str, model: str = "sonnet", binary: str = "claude",
                 timeout: int = 900, runner=None):
        if not oauth_token:
            raise LLMError("CLAUDE_CODE_OAUTH_TOKEN is not set")
        self.binary = binary
        if runner is None and not shutil.which(binary):
            raise LLMError("the `claude` CLI is not installed (npm install -g @anthropic-ai/claude-code)")
        self.model = model
        self.timeout = timeout
        self.runner = runner or subprocess.run
        self.env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        self.env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
        self.usage = {"calls": 0, "turns": 0, "est_cost_usd": 0.0}
        # Run from an empty folder so the repo's CLAUDE.md, hooks or settings never leak into prompts.
        self.workdir = tempfile.mkdtemp(prefix="cc-")

    def _cmd(self, system: str, web_search: bool, max_turns: int) -> list[str]:
        tools = "WebSearch,WebFetch" if web_search else ""
        cmd = [self.binary, "-p", "--output-format", "json", "--model", self.model,
               "--no-session-persistence", "--system-prompt", system,
               "--tools", tools, "--max-turns", str(max_turns)]
        if web_search:
            cmd += ["--allowedTools", "WebSearch,WebFetch"]
        return cmd

    def _once(self, system: str, prompt: str, web_search: bool, max_turns: int) -> str:
        last_err = ""
        for attempt in range(3):
            proc = self.runner(self._cmd(system, web_search, max_turns), input=prompt, capture_output=True,
                               text=True, timeout=self.timeout, env=self.env, cwd=self.workdir)
            out = (proc.stdout or "").strip()
            payload = None
            if out:
                try:
                    payload = json.loads(out.splitlines()[-1]) if not out.startswith("{") else json.loads(out)
                except json.JSONDecodeError:
                    payload = None
            if isinstance(payload, dict):
                self.usage["calls"] += 1
                self.usage["turns"] += payload.get("num_turns") or 0
                self.usage["est_cost_usd"] += payload.get("total_cost_usd") or 0.0
                if not payload.get("is_error") and proc.returncode == 0 and isinstance(payload.get("result"), str):
                    return payload["result"]
                last_err = f"{payload.get('subtype')}: {str(payload.get('result'))[:300]}"
            else:
                last_err = f"exit {proc.returncode}: {(proc.stderr or out)[:300]}"
            low = last_err.lower()
            if any(s in low for s in ("usage limit", "rate limit", "authenticat", "invalid bearer", "oauth", "401")):
                break  # retrying won't help
            log.warning("claude CLI call failed (attempt %d): %s", attempt + 1, last_err)
            time.sleep(10 * (attempt + 1))
        raise LLMError(f"Claude Code call failed: {last_err}")

    def ask_json(self, system: str, prompt: str, *, web_search: bool = False,
                 max_searches: int = 5, max_tokens: int = 12000, retries: int = 2):
        full_prompt = prompt + (SOURCES_NOTE + f"\nUse at most {max_searches} web searches." if web_search else "")
        max_turns = max_searches * 2 + 6 if web_search else 3
        text = self._once(system, full_prompt, web_search, max_turns)
        for attempt in range(retries + 1):
            try:
                data = extract_json(text)
                srcs = data.get("sources") if isinstance(data, dict) else None
                sources = [s for s in srcs if isinstance(s, dict) and s.get("url")] if isinstance(srcs, list) else []
                return data, sources
            except ValueError:
                if attempt == retries:
                    break
                log.warning("No valid JSON in Claude Code reply (attempt %d); asking again", attempt + 1)
                text = self._once(system, "The text below was supposed to be a single JSON object but is not "
                                          "valid JSON. Return ONLY the corrected, complete JSON object in a "
                                          "```json code block.\n\n" + text, False, 3)
        raise LLMError(f"model never returned valid JSON. Last output starts: {text[:300]!r}")


def make_llm(cfg):
    """Prefer the Claude subscription (free for the owner); fall back to an API key if that is what's set."""
    if cfg.claude_code_oauth_token:
        log.info("Using Claude Code with the Claude subscription (model %s)", cfg["claude_code_model"])
        return ClaudeCode(cfg.claude_code_oauth_token, cfg["claude_code_model"])
    if cfg.anthropic_api_key:
        log.info("Using the Claude API key (model %s)", cfg["claude_model"])
        return Claude(cfg.anthropic_api_key, cfg["claude_model"])
    raise LLMError("set CLAUDE_CODE_OAUTH_TOKEN (Claude subscription) or ANTHROPIC_API_KEY")
