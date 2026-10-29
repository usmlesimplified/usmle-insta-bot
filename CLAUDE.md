# Project context: @usmle.simplified Instagram autopilot

This is a standalone work project. Everything needed to continue it lives in this folder, so new sessions
don't depend on other chats.

## What it is
- Account: @usmle.simplified, display name "Saadi | USMLE Simplified". Audience: USMLE Step 1 students and IMGs.
  Goal: a daily educational post that brings leads for 1:1 Step 1 mentoring (CTA: DM "STEP1").
- The pipeline runs daily on GitHub Actions with no human review: pick topic -> web research -> write carousel
  -> fact-check -> render slides -> host images -> publish carousel + story.

## Map of the code
| File | Role |
|---|---|
| `bot/main.py` | Orchestrates the daily run (CLI flags: `--dry-run`, `--sample`, `--topic`, `--date`, `--force`, `--no-story`) |
| `bot/topics.py`, `bot/topic_bank.py` | Weekly series themes, ~200 weighted topics, 150-day no-repeat cooldown |
| `bot/content.py` | Claude prompts: research (web search), writing, repair, fact-check |
| `bot/llm.py` | `ClaudeCode` backend (default: `claude -p` CLI on the owner's subscription via `CLAUDE_CODE_OAUTH_TOKEN`, WebSearch/WebFetch tools) and `Claude` API fallback; `make_llm()` picks one |
| `bot/schema.py` | Post JSON shape and character limits (sized to what the slides can fit) |
| `bot/render.py` | Pillow renderer: 1080x1350 slides + 1080x1920 story, auto-sizing text |
| `bot/caption.py` | SEO caption, exactly 5 hashtags (Instagram cap since Dec 2025), alt text |
| `bot/hosting.py` | Images hosted on the repo's `media` branch (one orphan commit per day, so repo doesn't grow) |
| `bot/instagram.py` | Instagram API with Instagram Login (graph.instagram.com, v26.0), no-duplicate publish, token refresh |
| `.github/workflows/` | `daily-post.yml` (13:47 UTC = 6:47 PM PKT) and `refresh-token.yml` (weekly) |
| `config.json` | Brand, colours, model, hashtags, CTA lines |
| `data/history.json` | Log of what was posted (drives the cooldown) |

## Decisions already made
- Hosting via public GitHub repo + `media` branch (Instagram only accepts public image URLs).
- Publish only on a clean fact-check "pass"; otherwise skip the day.
- Carousel + story daily. Reels are not built yet.
- 2026-10-29: owner chose NOT to pay for the API. Claude runs on his Claude subscription via `claude setup-token` (token valid 1 year). Never run the CLI with `--bare` (it ignores the OAuth token).

## Status (2026-10-29)
- Code complete; 22 offline tests pass (`python -m pytest -q`). Not yet run against the real Claude or Instagram APIs.
- Next: owner completes one-time setup in README (Meta app token, Claude subscription token via `claude setup-token`, public GitHub repo, secrets),
  then a Dry run from GitHub Actions, then go live.
- Setup progress: step 1 (Instagram Professional) done.
- Ideas for later: Reels (video pipeline), logo at `assets/logo.png`, weekly performance report from Instagram insights.

## Working conventions
- Owner prefers replies in code-switched Roman Urdu + English, with tables.
- Verify any Instagram/Claude API detail against current docs before changing integration code.
- Run `python -m bot.main --sample --dry-run` and look at `out/<date>/` slides after any renderer change.
