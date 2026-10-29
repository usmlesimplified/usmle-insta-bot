"""Daily pipeline: pick topic -> research -> write -> fact-check -> render -> host -> publish.

Usage:
  python -m bot.main                  # full autonomous run (what GitHub Actions does daily)
  python -m bot.main --dry-run        # everything except hosting/publishing; output in out/<date>/
  python -m bot.main --sample --dry-run   # no API calls at all: renders samples/sample_post.json
  python -m bot.main --topic "Lesch-Nyhan syndrome"   # force a topic for today
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .caption import alt_text_for, build_caption
from .config import DATA_DIR, OUT_DIR, ROOT, load_config
from .content import ContentRejected, ensure_valid, fact_check, produce_post, research_topic, shorten_slides
from .render import SlideRenderer
from .topics import (load_history, posted_on, recent_titles, save_history, select_candidates, theme_for)

log = logging.getLogger("bot")
HISTORY = DATA_DIR / "history.json"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="USMLE Step 1 Instagram autopilot")
    p.add_argument("--dry-run", action="store_true", help="do not upload or publish")
    p.add_argument("--sample", action="store_true", help="use samples/sample_post.json instead of calling Claude")
    p.add_argument("--topic", default=None, help="force today's topic")
    p.add_argument("--date", default=None, help="pretend today is YYYY-MM-DD (testing)")
    p.add_argument("--force", action="store_true", help="post even if something was already posted today")
    p.add_argument("--no-story", action="store_true", help="skip the story")
    return p.parse_args(argv)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def run(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    today = date.fromisoformat(args.date) if args.date else datetime.now(tz).date()
    out = OUT_DIR / today.isoformat()

    history = load_history(HISTORY)
    already = posted_on(history, today)
    if already and not args.force and not args.dry_run:
        log.info("Already posted today (%s: %s). Nothing to do.", already.get("title"), already.get("permalink"))
        return 0

    theme = theme_for(today)
    log.info("Date %s | series: %s", today, theme.series)

    llm = None
    review_log = []
    if args.sample:
        post = json.loads((ROOT / "samples" / "sample_post.json").read_text(encoding="utf-8"))
        brief = {"topic": post["title"], "discipline": "Pharmacology", "chosen_id": "sample", "sources": []}
    else:
        from . import llm as llm_mod
        llm = llm_mod.make_llm(cfg)
        candidates = select_candidates(history, theme, today, k=cfg["candidates_per_day"],
                                       cooldown_days=cfg["topic_cooldown_days"])
        tried = set()
        post = None
        for attempt in range(2):  # if the fact-checker rejects a topic, try a different one once
            pool = [c for c in candidates if c["id"] not in tried] or candidates
            brief = research_topic(llm, cfg, theme, today, pool, recent_titles(history), forced_topic=args.topic)
            tried.add(brief.get("chosen_id"))
            log.info("Topic: %s (%s) - %s", brief.get("topic"), brief.get("format"), brief.get("why_today"))
            write_json(out / "brief.json", brief)
            try:
                post, review_log = produce_post(llm, cfg, theme, brief)
                break
            except ContentRejected as e:
                log.warning("Content rejected: %s", e)
                if args.topic:
                    raise
        if post is None:
            raise ContentRejected("two topics in a row failed fact-checking; not posting today")

    label = brief.get("discipline") or theme.name
    if label == "Mixed":
        label = "Step 1"

    # Render; if any slide is too dense to read, ask Claude to tighten it (max twice).
    renderer = SlideRenderer(cfg)
    edited_after_check = False
    for attempt in range(3):
        paths, overflow = renderer.render_post(post, out, label)
        if not overflow:
            break
        if llm is None or attempt == 2:
            raise RuntimeError(f"text does not fit on slide(s) {overflow}; not publishing a hard-to-read post")
        log.info("Slides %s too dense, shortening", overflow)
        post = ensure_valid(llm, shorten_slides(llm, post, overflow))
        edited_after_check = True

    if edited_after_check:
        # Shortening rewrote text after the fact-check, so check once more before publishing.
        review = fact_check(llm, cfg, post, brief)
        review_log.append({"round": "post-shorten", "verdict": review.get("verdict"), "issues": review.get("issues", [])})
        verdict = str(review.get("verdict", "")).lower()
        if verdict == "fix" and isinstance(review.get("corrected_post"), dict):
            post = ensure_valid(llm, review["corrected_post"])
            paths, overflow = renderer.render_post(post, out, label)
            if overflow:
                raise RuntimeError(f"corrected post does not fit on slide(s) {overflow}")
        elif verdict != "pass":
            raise ContentRejected(f"shortened post failed fact-check: {review.get('issues')}")
    story_path = None
    if cfg["post_story"] and not args.no_story:
        story_path = renderer.render_story(post, out / "story.jpg", theme.series)

    caption = build_caption(post, cfg)
    alts = [alt_text_for(s, i, len(post["slides"]), post["title"]) for i, s in enumerate(post["slides"], 1)]
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    write_json(out / "post.json", post)
    write_json(out / "review.json", review_log)
    if llm:
        log.info("Claude usage: %s", llm.usage)

    if args.dry_run:
        log.info("DRY RUN complete. Files in %s", out)
        print(f"\n--- CAPTION ({len(caption)} chars) ---\n{caption}\n")
        return 0

    # ---- publish
    from .hosting import GitHubImageHost
    from .instagram import Instagram

    host = GitHubImageHost(cfg.github_repository, cfg.github_token, cfg.media_branch)
    files = list(paths) + ([story_path] if story_path else [])
    urls = host.upload_all(files, f"posts/{today.isoformat()}")
    slide_urls = urls[: len(paths)]
    story_url = urls[len(paths)] if story_path else None

    ig = Instagram(cfg.ig_access_token, cfg.ig_user_id, cfg["graph_api_version"])
    try:
        quota = ig.publishing_quota()
        log.info("Publishing quota: %s", quota)
    except Exception as e:  # informational only
        log.info("Could not read publishing quota: %s", e)

    media_id, permalink = ig.publish_carousel(slide_urls, alts, caption, ai_label=cfg["mark_ai_generated"])
    log.info("Published carousel %s %s", media_id, permalink or "")

    entry = {
        "date": today.isoformat(),
        "topic_id": brief.get("chosen_id") if brief.get("chosen_id") not in ("trending", "forced", None) else None,
        "topic": brief.get("topic"),
        "title": post.get("title"),
        "discipline": brief.get("discipline"),
        "series": theme.series,
        "format": post.get("format"),
        "media_id": media_id,
        "permalink": permalink,
        "sources": [s.get("url") for s in brief.get("sources", [])][:8],
    }
    history.setdefault("posts", []).append(entry)
    save_history(HISTORY, history)

    if story_url:
        try:
            sid = ig.publish_story(story_url)
            entry["story_id"] = sid
            save_history(HISTORY, history)
            log.info("Published story %s", sid)
        except Exception as e:  # the post is live; a failed story should not fail the run
            log.warning("Story failed (post is live): %s", e)
    return 0


def main():
    try:
        sys.exit(run())
    except Exception as e:
        logging.getLogger("bot").error("RUN FAILED: %s", e)
        raise


if __name__ == "__main__":
    main()
