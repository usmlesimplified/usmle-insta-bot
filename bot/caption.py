"""Caption + hashtag assembly (Instagram: max 5 hashtags, 2,200 characters)."""

import re

MAX_CAPTION = 2200
MAX_HASHTAGS = 5


def clean_tag(tag: str) -> str | None:
    t = re.sub(r"[^a-z0-9_]", "", (tag or "").lower().lstrip("#"))
    return f"#{t}" if t else None


def build_hashtags(anchor: list[str], topic_tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in list(anchor) + list(topic_tags):
        c = clean_tag(t)
        if c and c not in out:
            out.append(c)
        if len(out) == MAX_HASHTAGS:
            break
    return out


def _no_hashtags(text: str) -> str:
    # Hashtags inside the body would push us over the 5-tag cap.
    return re.sub(r"(?<!\w)#(\w+)", r"\1", text or "").strip()


def build_caption(post: dict, cfg) -> str:
    cap = post.get("caption", {})
    hook = _no_hashtags(cap.get("hook", ""))
    body = [_no_hashtags(x) for x in cap.get("body", []) if x and x.strip()]
    question = _no_hashtags(cap.get("question", ""))
    tags = build_hashtags(cfg["anchor_hashtags"], post.get("hashtags", []))

    parts = [hook, "", *body]
    if question:
        parts += ["", f"{question} 👇"]
    parts += [
        "",
        "📌 Save this for revision",
        f"🔁 Share with your Step 1 study partner",
        f"🩺 {cfg['cta_mentoring_line']}",
        "",
        cfg["caption_disclaimer"],
        "",
        " ".join(tags),
    ]
    caption = "\n".join(parts).strip()
    if len(caption) > MAX_CAPTION:
        # Trim body lines from the end until it fits; never cut CTA/hashtags.
        while body and len(caption) > MAX_CAPTION:
            body.pop()
            caption = build_caption({**post, "caption": {**cap, "body": body}}, cfg)
    return caption


def alt_text_for(slide: dict, idx: int, total: int, title: str) -> str:
    def s(x):
        return re.sub(r"\*\*", "", x or "")

    t = slide.get("type")
    if t == "cover":
        text = f"{s(slide.get('headline'))}. {s(slide.get('subtitle'))}"
    elif t == "bullets":
        text = f"{s(slide.get('heading'))}: " + "; ".join(s(b) for b in slide.get("bullets", []))
    elif t == "table":
        cols = slide.get("columns", [])
        rows = ["; ".join(f"{s(c)}: {s(v)}" for c, v in zip(cols, r)) for r in slide.get("rows", [])]
        text = f"Table, {s(slide.get('heading'))}. " + " | ".join(rows)
    elif t == "mnemonic":
        text = f"Mnemonic {s(slide.get('mnemonic'))}: " + "; ".join(
            f"{s(i.get('letter'))} = {s(i.get('meaning'))}" for i in slide.get("items", []))
    elif t == "question":
        opts = "; ".join(f"{'ABCDE'[k]}) {s(o)}" for k, o in enumerate(slide.get("options", [])))
        text = f"Practice question: {s(slide.get('stem'))} Options: {opts}"
    elif t == "answer":
        text = f"Answer {slide.get('correct')}: {s(slide.get('answer_text'))}. {s(slide.get('explanation'))}"
    elif t == "pearl":
        text = f"{s(slide.get('heading'))}: {s(slide.get('text'))} {s(slide.get('detail'))}"
    else:
        text = "Save this post and follow for daily USMLE Step 1 high-yield content."
    full = f"USMLE Step 1, {title}, slide {idx} of {total}. {text}".strip()
    return full[:1000]
