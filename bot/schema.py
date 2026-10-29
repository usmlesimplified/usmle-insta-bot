"""Post schema, length limits (sized to what the slide renderer can fit) and validation."""

import re

LIMITS = {
    "cover": {"kicker": 40, "headline": 70, "subtitle": 110},
    "bullets": {"heading": 50, "bullets_min": 2, "bullets_max": 6, "bullet": 120},
    "table": {"heading": 50, "cols_min": 2, "cols_max": 3, "rows_min": 2, "rows_max": 6, "cell": 70, "column": 24},
    "mnemonic": {"heading": 50, "mnemonic": 40, "items_min": 2, "items_max": 8, "letter": 3, "meaning": 80},
    "question": {"stem": 420, "options_min": 4, "options_max": 5, "option": 90},
    "answer": {"answer_text": 90, "explanation": 360, "distractors_max": 4, "distractor": 110},
    "pearl": {"heading": 50, "text": 220, "detail": 200},
    "caption": {"hook": 125, "body_min": 2, "body_max": 6, "body_line": 220, "question": 150},
    "story": {"headline": 60, "teaser": 120},
    "slides_min": 5,
    "slides_max": 10,
    "topic_hashtags": 3,
}

SLIDE_TYPES = ("cover", "bullets", "table", "mnemonic", "question", "answer", "pearl", "cta")
LETTERS = "ABCDE"

SCHEMA_EXAMPLE = """{
  "title": "Protein Synthesis Inhibitors",
  "format": "mnemonic",
  "slides": [
    {"type": "cover", "kicker": "PHARM TUESDAY", "headline": "Buy **AT 30**, CCEL at **50**", "subtitle": "One mnemonic for every ribosome question on NBME"},
    {"type": "mnemonic", "heading": "The mnemonic", "mnemonic": "Buy AT 30, CCEL at 50", "items": [{"letter": "A", "meaning": "**Aminoglycosides** (30S)"}]},
    {"type": "table", "heading": "Mechanism + toxicity", "columns": ["Drug", "Mechanism", "Classic toxicity"], "rows": [["Aminoglycosides", "...", "..."]]},
    {"type": "bullets", "heading": "Resistance quick hits", "bullets": ["**Aminoglycosides**: inactivated by bacterial transferases"]},
    {"type": "pearl", "variant": "trap", "heading": "NBME trap", "text": "...", "detail": "optional one-line extra"},
    {"type": "question", "stem": "A 45-year-old man ...", "options": ["...", "...", "...", "...", "..."]},
    {"type": "answer", "correct": "C", "answer_text": "...", "explanation": "...", "distractors": ["A: why wrong", "B: why wrong"]},
    {"type": "cta"}
  ],
  "caption": {
    "hook": "SEO first line with the main keyword, under 125 characters",
    "body": ["2-6 short lines that restate the key teaching points with natural keywords"],
    "question": "A comment prompt that invites an answer"
  },
  "hashtags": ["#pharmacology", "#antibiotics", "#medstudent"],
  "story": {"headline": "short story headline", "teaser": "one-line hook that sends people to the post"}
}"""


def limits_text() -> str:
    L = LIMITS
    return f"""HARD LIMITS (characters, count every character; the slides physically cannot fit more):
- 5 to 10 slides total. First slide type "cover", last slide type "cta" ({{"type": "cta"}} only, no other keys).
- cover: kicker <= {L['cover']['kicker']}, headline <= {L['cover']['headline']}, subtitle <= {L['cover']['subtitle']}.
- bullets: heading <= {L['bullets']['heading']}; {L['bullets']['bullets_min']}-{L['bullets']['bullets_max']} bullets, each <= {L['bullets']['bullet']}.
- table: heading <= {L['table']['heading']}; {L['table']['cols_min']}-{L['table']['cols_max']} columns (header <= {L['table']['column']} each); {L['table']['rows_min']}-{L['table']['rows_max']} rows; every row has exactly one cell per column; each cell <= {L['table']['cell']}.
- mnemonic: heading <= {L['mnemonic']['heading']}; mnemonic phrase <= {L['mnemonic']['mnemonic']}; {L['mnemonic']['items_min']}-{L['mnemonic']['items_max']} items; letter <= {L['mnemonic']['letter']}; meaning <= {L['mnemonic']['meaning']}.
- question: stem <= {L['question']['stem']}; {L['question']['options_min']}-{L['question']['options_max']} options (no "A." prefixes, letters are added automatically), each <= {L['question']['option']}. A question slide MUST be immediately followed by its answer slide.
- answer: correct = one letter; answer_text <= {L['answer']['answer_text']}; explanation <= {L['answer']['explanation']}; distractors optional, max {L['answer']['distractors_max']}, each <= {L['answer']['distractor']} and starting with the option letter.
- pearl: variant "pearl" or "trap"; heading <= {L['pearl']['heading']}; text <= {L['pearl']['text']}; detail optional <= {L['pearl']['detail']}.
- caption: hook <= {L['caption']['hook']}; body {L['caption']['body_min']}-{L['caption']['body_max']} lines each <= {L['caption']['body_line']}; question <= {L['caption']['question']}. NO hashtags anywhere in the caption.
- hashtags: exactly {L['topic_hashtags']} topic-specific hashtags (lowercase, no spaces), NOT #usmle or #usmlestep1 (those are added automatically).
- story: headline <= {L['story']['headline']}, teaser <= {L['story']['teaser']}.
- Wrap key terms in **double asterisks** for bold (works in all slide text fields). Do not use emojis on slides. Use plain arrows like -> or the symbols ↑ ↓ → when useful."""


def _strip_md(s: str) -> str:
    return re.sub(r"\*\*", "", s or "")


def _len(s) -> int:
    return len(_strip_md(s if isinstance(s, str) else ""))


def validate_post(post: dict) -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errs: list[str] = []
    L = LIMITS
    if not isinstance(post, dict):
        return ["post is not a JSON object"]
    if not isinstance(post.get("title"), str) or not post["title"].strip():
        errs.append("missing title")
    slides = post.get("slides")
    if not isinstance(slides, list):
        return errs + ["slides must be a list"]
    if not (L["slides_min"] <= len(slides) <= L["slides_max"]):
        errs.append(f"need {L['slides_min']}-{L['slides_max']} slides, got {len(slides)}")
    if slides and slides[0].get("type") != "cover":
        errs.append("first slide must be cover")
    if slides and slides[-1].get("type") != "cta":
        errs.append("last slide must be cta")

    def chk(i, field, value, limit):
        if not isinstance(value, str) or not value.strip():
            errs.append(f"slide {i} ({field}) is empty")
        elif _len(value) > limit:
            errs.append(f"slide {i} {field} is {_len(value)} chars (max {limit})")

    for i, s in enumerate(slides, 1):
        t = s.get("type") if isinstance(s, dict) else None
        if t not in SLIDE_TYPES:
            errs.append(f"slide {i} has unknown type {t!r}")
            continue
        if t == "cover":
            for f in ("kicker", "headline", "subtitle"):
                chk(i, f, s.get(f), L["cover"][f])
        elif t == "bullets":
            chk(i, "heading", s.get("heading"), L["bullets"]["heading"])
            b = s.get("bullets") or []
            if not (L["bullets"]["bullets_min"] <= len(b) <= L["bullets"]["bullets_max"]):
                errs.append(f"slide {i} needs {L['bullets']['bullets_min']}-{L['bullets']['bullets_max']} bullets")
            for x in b:
                chk(i, "bullet", x, L["bullets"]["bullet"])
        elif t == "table":
            chk(i, "heading", s.get("heading"), L["table"]["heading"])
            cols = s.get("columns") or []
            rows = s.get("rows") or []
            if not (L["table"]["cols_min"] <= len(cols) <= L["table"]["cols_max"]):
                errs.append(f"slide {i} needs {L['table']['cols_min']}-{L['table']['cols_max']} columns")
            for c in cols:
                chk(i, "column header", c, L["table"]["column"])
            if not (L["table"]["rows_min"] <= len(rows) <= L["table"]["rows_max"]):
                errs.append(f"slide {i} needs {L['table']['rows_min']}-{L['table']['rows_max']} rows")
            for r in rows:
                if not isinstance(r, list) or len(r) != len(cols):
                    errs.append(f"slide {i} has a row with the wrong number of cells")
                    continue
                for c in r:
                    chk(i, "cell", c, L["table"]["cell"])
        elif t == "mnemonic":
            chk(i, "heading", s.get("heading"), L["mnemonic"]["heading"])
            chk(i, "mnemonic", s.get("mnemonic"), L["mnemonic"]["mnemonic"])
            items = s.get("items") or []
            if not (L["mnemonic"]["items_min"] <= len(items) <= L["mnemonic"]["items_max"]):
                errs.append(f"slide {i} needs {L['mnemonic']['items_min']}-{L['mnemonic']['items_max']} mnemonic items")
            for it in items:
                if not isinstance(it, dict):
                    errs.append(f"slide {i} mnemonic item is not an object")
                    continue
                chk(i, "letter", it.get("letter"), L["mnemonic"]["letter"])
                chk(i, "meaning", it.get("meaning"), L["mnemonic"]["meaning"])
        elif t == "question":
            chk(i, "stem", s.get("stem"), L["question"]["stem"])
            opts = s.get("options") or []
            if not (L["question"]["options_min"] <= len(opts) <= L["question"]["options_max"]):
                errs.append(f"slide {i} needs {L['question']['options_min']}-{L['question']['options_max']} options")
            for o in opts:
                chk(i, "option", o, L["question"]["option"])
            nxt = slides[i] if i < len(slides) else None
            if not (isinstance(nxt, dict) and nxt.get("type") == "answer"):
                errs.append(f"slide {i} question must be followed by an answer slide")
            elif str(nxt.get("correct", "")).strip().upper() not in LETTERS[: len(opts)]:
                errs.append(f"slide {i + 1} answer letter does not match an option")
        elif t == "answer":
            chk(i, "answer_text", s.get("answer_text"), L["answer"]["answer_text"])
            chk(i, "explanation", s.get("explanation"), L["answer"]["explanation"])
            d = s.get("distractors") or []
            if len(d) > L["answer"]["distractors_max"]:
                errs.append(f"slide {i} has too many distractor notes")
            for x in d:
                chk(i, "distractor", x, L["answer"]["distractor"])
            prev = slides[i - 2] if i >= 2 else None
            if not (isinstance(prev, dict) and prev.get("type") == "question"):
                errs.append(f"slide {i} answer must come right after a question")
        elif t == "pearl":
            if s.get("variant") not in ("pearl", "trap"):
                errs.append(f"slide {i} pearl variant must be 'pearl' or 'trap'")
            chk(i, "heading", s.get("heading"), L["pearl"]["heading"])
            chk(i, "text", s.get("text"), L["pearl"]["text"])
            if s.get("detail"):
                chk(i, "detail", s.get("detail"), L["pearl"]["detail"])

    cap = post.get("caption") or {}
    if not isinstance(cap, dict):
        errs.append("caption must be an object")
        cap = {}
    if not isinstance(cap.get("hook"), str) or not cap["hook"].strip():
        errs.append("caption.hook is empty")
    elif len(cap["hook"]) > L["caption"]["hook"]:
        errs.append(f"caption.hook is {len(cap['hook'])} chars (max {L['caption']['hook']})")
    body = cap.get("body") or []
    if not (L["caption"]["body_min"] <= len(body) <= L["caption"]["body_max"]):
        errs.append(f"caption.body needs {L['caption']['body_min']}-{L['caption']['body_max']} lines")
    for line in body:
        if not isinstance(line, str) or len(line) > L["caption"]["body_line"]:
            errs.append("caption.body line too long or not text")
    if not isinstance(cap.get("question"), str) or len(cap.get("question", "")) > L["caption"]["question"]:
        errs.append("caption.question missing or too long")

    tags = post.get("hashtags") or []
    if len(tags) != L["topic_hashtags"]:
        errs.append(f"need exactly {L['topic_hashtags']} topic hashtags, got {len(tags)}")

    story = post.get("story") or {}
    for f in ("headline", "teaser"):
        v = story.get(f) if isinstance(story, dict) else None
        if not isinstance(v, str) or not v.strip() or len(v) > L["story"][f]:
            errs.append(f"story.{f} missing or too long")
    return errs
