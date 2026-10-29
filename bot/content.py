"""Topic research, post writing, fact-checking and repair — all via Claude."""

import json
import logging
from datetime import date

from .schema import SCHEMA_EXAMPLE, limits_text, validate_post
from .topics import Theme

log = logging.getLogger(__name__)

FACULTY = (
    "You are a senior USMLE Step 1 faculty member and NBME-style item writer who also runs a fast-growing "
    "educational Instagram account for medical students and IMGs. You are precise: every fact must match "
    "current standard Step 1 references (First Aid, Pathoma, Sketchy-level facts, StatPearls/NCBI, FDA labels, "
    "CDC). Use US drug names and US spelling. Never invent statistics or sources."
)


# ---------------------------------------------------------------- research

def research_topic(llm, cfg, theme: Theme, day: date, candidates: list[dict], recent: list[str],
                   forced_topic: str | None = None):
    """Pick today's topic (or use the forced one) and build a research brief using web search."""
    if forced_topic:
        choose = (f'Today\'s topic is fixed by the account owner: "{forced_topic}". Do not pick another topic.')
    else:
        cand_lines = "\n".join(f'- id="{c["id"]}" | {c["discipline"]} | {c["topic"]} (yield {c["weight"]}/3)'
                               for c in candidates)
        choose = f"""Choose ONE topic for today's post.
Candidates (pre-filtered so nothing repeats):
{cand_lines}

Step A - relevance check (use 1-3 searches): look for anything from the last few weeks that makes one candidate
especially timely for Step 1 students (an outbreak in the news, a new FDA approval or boxed warning tied to a
Step 1 mechanism, USMLE/NBME announcements, a topic students are actively discussing). If something is clearly
timely AND Step 1-testable you may instead propose ONE new topic within the theme's disciplines; set
chosen_id to "trending". Otherwise pick the candidate with the best mix of yield and engagement potential."""

    prompt = f"""Date: {day.isoformat()}. Today's series: "{theme.series}" (disciplines: {", ".join(theme.disciplines)}).
Allowed post formats today: {", ".join(theme.formats)} (first one preferred).
Recent posts on the account (avoid overlap): {json.dumps(recent[:15], ensure_ascii=False)}

{choose}

Step B - refine (use 2-4 searches): research the chosen topic against authoritative sources to confirm the exact
high-yield facts, the classic NBME-style traps/confusions, and the keywords students search for
(e.g. "cephalosporin generations mnemonic", "30S vs 50S antibiotics").

Return ONLY a JSON object:
{{
  "chosen_id": "<candidate id, or 'trending', or 'forced'>",
  "topic": "<final topic title>",
  "discipline": "<discipline>",
  "why_today": "<one sentence>",
  "format": "<one of: {", ".join(theme.formats)}>",
  "angle": "<the hook/angle that will make students save this post>",
  "key_facts": ["<8-14 verified, exam-relevant facts, each one line>"],
  "common_traps": ["<2-5 classic confusions or NBME traps>"],
  "mnemonic_ideas": ["<0-3 established mnemonics if any; only well-known or obviously self-explanatory ones>"],
  "seo_keywords": ["<5-8 phrases students actually search, lowercase>"]
}}"""
    brief, sources = llm.ask_json(FACULTY, prompt, web_search=True,
                                  max_searches=cfg["research_max_searches"], max_tokens=8000)
    brief["sources"] = sources[:12]
    if forced_topic:
        brief["chosen_id"] = "forced"
    return brief


# ---------------------------------------------------------------- writing

FORMAT_GUIDE = {
    "table": "Core of the post is 1-2 comparison TABLE slides. Add a pearl/trap slide and ideally a mini vignette (question + answer).",
    "mnemonic": "Core of the post is a MNEMONIC slide (only use established or obviously self-explanatory mnemonics) followed by a table or bullets that unpack it. Add a pearl/trap slide.",
    "concept": "Explain the concept step by step with 2-3 bullets/table slides building on each other, then a trap slide and a mini vignette (question + answer).",
    "quiz": "Lead with an NBME-style vignette right after the cover (question then answer slide), then 2-3 teaching slides (table/bullets/pearl) that cover the concept behind it.",
}


def write_post(llm, cfg, theme: Theme, brief: dict) -> dict:
    fmt = brief.get("format") if brief.get("format") in theme.formats else theme.formats[0]
    prompt = f"""Write today's Instagram carousel for {cfg['handle']} ({cfg['brand_name']}), a USMLE Step 1 tutoring account.
Series: "{theme.series}". Format: {fmt}. {FORMAT_GUIDE[fmt]}

Research brief (verified facts - stick to these, do not add unverified claims):
{json.dumps({k: v for k, v in brief.items() if k != "sources"}, indent=2, ensure_ascii=False)}

Writing rules:
- Audience: Step 1 students and IMGs. High-yield only, no fluff, exam-style phrasing.
- Cover: kicker = the series name in caps (e.g. "{theme.series.upper()}"); headline is a scroll-stopping hook with 1-2 **bold** words; subtitle promises the payoff.
- Every content slide teaches something testable. Tables: short cells, parallel phrasing.
- Vignettes: NBME style (age/sex, presentation, findings, single best answer), exactly one defensible correct answer, plausible distractors.
- Caption is SEO: the hook's first words contain the main search keyword (e.g. "Protein synthesis inhibitors for USMLE Step 1: ..."), body lines naturally use the seo_keywords, end with a question that invites comments. Emojis allowed in caption only (max 4).
- Hashtags: 3 specific niche tags for this topic (e.g. #pharmacology #microbiology #medstudent #img #usmleprep #firstaidstep1 style), lowercase.

{limits_text()}

Return ONLY the JSON object, exactly in this shape:
{SCHEMA_EXAMPLE}"""
    post, _ = llm.ask_json(FACULTY, prompt, web_search=False, max_tokens=8000)
    post.setdefault("format", fmt)
    return post


def repair_post(llm, post: dict, problems: list[str]) -> dict:
    prompt = f"""This Instagram carousel JSON breaks the format rules. Fix ONLY what is needed (shorten or restructure),
keep the medical content identical in meaning.

Problems:
{json.dumps(problems, indent=2)}

{limits_text()}

JSON to fix:
{json.dumps(post, indent=2, ensure_ascii=False)}

Return ONLY the corrected JSON object with the same shape."""
    fixed, _ = llm.ask_json(FACULTY, prompt, web_search=False, max_tokens=8000)
    return fixed


def shorten_slides(llm, post: dict, slide_numbers: list[int]) -> dict:
    prompt = f"""When rendered, the text on slide(s) {slide_numbers} (1-based) does not fit on the image.
Shorten the text on those slides by about 25-35% (fewer words, tighter phrasing, drop the least important
row/bullet if needed). Keep medical meaning correct. Leave every other slide unchanged.

{limits_text()}

JSON:
{json.dumps(post, indent=2, ensure_ascii=False)}

Return ONLY the full corrected JSON object."""
    fixed, _ = llm.ask_json(FACULTY, prompt, web_search=False, max_tokens=8000)
    return fixed


# ---------------------------------------------------------------- fact check

REVIEWER = (
    "You are a strict NBME item reviewer and medical fact-checker. You catch subtle errors: wrong drug-mechanism "
    "pairings, outdated guidelines, mnemonic letters that do not map to the right items, vignettes with two "
    "defensible answers, wrong associations, overstated claims. You are not satisfied with 'mostly right'."
)


def fact_check(llm, cfg, post: dict, brief: dict) -> dict:
    prompt = f"""Fact-check this Instagram carousel for a USMLE Step 1 audience before it is published automatically
(no human will review it). Check EVERY factual statement on every slide and in the caption. Use web search to
verify anything you are not certain about (max a few searches).

Also check: mnemonic letters map correctly; each table row is correct for its column headers; vignette has
exactly one best answer and the answer slide letter matches the right option; nothing is outdated or harmful.

Topic brief for context:
{json.dumps({k: brief.get(k) for k in ("topic", "key_facts", "common_traps")}, indent=2, ensure_ascii=False)}

Post JSON:
{json.dumps(post, indent=2, ensure_ascii=False)}

Return ONLY JSON:
{{
  "verdict": "pass" | "fix" | "reject",
  "issues": ["<each problem found, specific>"],
  "corrected_post": <null if pass or reject; otherwise the FULL post JSON with every issue fixed, same shape and limits>
}}
Use "reject" only if the topic or post cannot be made accurate with edits.

{limits_text()}"""
    result, sources = llm.ask_json(REVIEWER, prompt, web_search=True,
                                   max_searches=cfg["factcheck_max_searches"], max_tokens=12000)
    result["sources"] = sources[:10]
    return result


def produce_post(llm, cfg, theme: Theme, brief: dict) -> tuple[dict, list[dict]]:
    """Write -> validate/repair -> fact-check (up to 2 rounds). Returns (post, review_log)."""
    review_log = []
    post = write_post(llm, cfg, theme, brief)
    post = ensure_valid(llm, post)

    for round_no in range(1, 3):
        review = fact_check(llm, cfg, post, brief)
        verdict = str(review.get("verdict", "")).lower()
        review_log.append({"round": round_no, "verdict": verdict, "issues": review.get("issues", []),
                           "sources": review.get("sources", [])})
        log.info("Fact-check round %d: %s (%d issues)", round_no, verdict, len(review.get("issues", [])))
        if verdict == "pass":
            return post, review_log
        if verdict == "reject":
            raise ContentRejected(f"fact-checker rejected the post: {review.get('issues')}")
        corrected = review.get("corrected_post")
        if not isinstance(corrected, dict):
            raise ContentRejected("fact-checker asked for fixes but returned no corrected post")
        post = ensure_valid(llm, corrected)
    # Two rounds of fixes and still not a clean pass: run one final check, accept only a pass.
    final = fact_check(llm, cfg, post, brief)
    review_log.append({"round": 3, "verdict": final.get("verdict"), "issues": final.get("issues", [])})
    if str(final.get("verdict", "")).lower() == "pass":
        return post, review_log
    raise ContentRejected(f"post did not pass fact-check after fixes: {final.get('issues')}")


def ensure_valid(llm, post: dict) -> dict:
    for _ in range(3):
        problems = validate_post(post)
        if not problems:
            return post
        log.info("Repairing %d format problems: %s", len(problems), problems[:5])
        post = repair_post(llm, post, problems)
    problems = validate_post(post)
    if problems:
        raise ContentRejected(f"post still breaks format rules: {problems}")
    return post


class ContentRejected(RuntimeError):
    pass
