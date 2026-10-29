"""Daily theme + candidate topic selection with a no-repeat cooldown."""

import json
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .topic_bank import BANK

ALL_DISCIPLINES = list(BANK.keys())


@dataclass(frozen=True)
class Theme:
    name: str            # shown on slides / used in prompts
    series: str          # recurring series label, good for brand recall
    disciplines: tuple   # disciplines to draw candidates from
    formats: tuple       # allowed post formats (first = preferred)


def theme_for(day: date) -> Theme:
    wd = day.weekday()  # Mon=0
    if wd == 0:
        return Theme("Microbiology", "Micro Monday", ("Microbiology",), ("table", "concept", "quiz"))
    if wd == 1:
        return Theme("Pharmacology", "Pharm Tuesday", ("Pharmacology",), ("table", "mnemonic", "concept"))
    if wd == 2:
        return Theme("Pathology", "Path Wednesday", ("Pathology",), ("concept", "table", "quiz"))
    if wd == 3:
        # Alternate physiology and biochem/genetics by ISO week.
        if day.isocalendar().week % 2 == 0:
            return Theme("Physiology", "Physio Thursday", ("Physiology",), ("concept", "table", "quiz"))
        return Theme("Biochemistry & Genetics", "Biochem Thursday", ("Biochemistry & Genetics",), ("table", "mnemonic", "concept"))
    if wd == 4:
        return Theme("Immunology", "Immuno Friday", ("Immunology",), ("table", "concept", "mnemonic"))
    if wd == 5:
        return Theme("Mixed", "Mnemonic Saturday", tuple(ALL_DISCIPLINES), ("mnemonic",))
    return Theme("Biostatistics & Ethics", "Sunday Vignette", ("Biostatistics & Ethics",), ("quiz",))


def topic_id(discipline: str, topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{discipline}-{topic}".lower()).strip("-")
    return slug[:90]


def all_topics():
    for discipline, items in BANK.items():
        for topic, weight in items:
            yield {"id": topic_id(discipline, topic), "discipline": discipline, "topic": topic, "weight": weight}


# ---------------- history ----------------

def load_history(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"posts": []}


def save_history(path: Path, history: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
        f.write("\n")


def posted_on(history: dict, day: date) -> dict | None:
    for p in history.get("posts", []):
        if p.get("date") == day.isoformat() and p.get("media_id"):
            return p
    return None


def last_used(history: dict) -> dict:
    """topic_id -> most recent date it was posted."""
    used = {}
    for p in history.get("posts", []):
        tid, d = p.get("topic_id"), p.get("date")
        if tid and d:
            dd = date.fromisoformat(d)
            if tid not in used or dd > used[tid]:
                used[tid] = dd
    return used


def recent_titles(history: dict, n: int = 20) -> list[str]:
    posts = sorted(history.get("posts", []), key=lambda p: p.get("date", ""), reverse=True)
    return [p.get("title") or p.get("topic") for p in posts[:n] if p.get("title") or p.get("topic")]


def select_candidates(history: dict, theme: Theme, day: date, k: int = 8, cooldown_days: int = 150) -> list[dict]:
    used = last_used(history)
    cutoff = day - timedelta(days=cooldown_days)
    pool = [t for t in all_topics() if t["discipline"] in theme.disciplines]
    fresh = [t for t in pool if used.get(t["id"]) is None or used[t["id"]] < cutoff]

    rng = random.Random(day.toordinal())  # reproducible per day
    chosen: list[dict] = []
    if fresh:
        # Weighted sampling without replacement (weight^1.5 favours very-high-yield).
        items = fresh[:]
        while items and len(chosen) < k:
            weights = [t["weight"] ** 1.5 for t in items]
            pick = rng.choices(items, weights=weights, k=1)[0]
            chosen.append(pick)
            items.remove(pick)
    if len(chosen) < k:
        # Not enough fresh topics: top up with the least recently used ones.
        rest = [t for t in pool if t not in chosen]
        rest.sort(key=lambda t: used.get(t["id"], date.min))
        chosen.extend(rest[: k - len(chosen)])
    return chosen
