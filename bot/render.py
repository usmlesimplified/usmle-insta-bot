"""Renders a post JSON into 1080x1350 JPEG carousel slides (+ a 1080x1920 story)."""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

from .config import ASSETS_DIR

log = logging.getLogger(__name__)

W, H = 1080, 1350          # 4:5 portrait, the tallest feed ratio Instagram allows
SW, SH = 1080, 1920        # story
M = 84                     # side margin
LH = 1.32                  # line-height factor
FONT_DIR = ASSETS_DIR / "fonts"


# ------------------------------------------------------------------ fonts

class Fonts:
    FILES = {
        "regular": "Poppins-Regular.ttf",
        "medium": "Poppins-Medium.ttf",
        "bold": "Poppins-Bold.ttf",
        "italic": "Poppins-Italic.ttf",
        "fb_regular": "DejaVuSans.ttf",
        "fb_bold": "DejaVuSans-Bold.ttf",
    }

    def __init__(self, font_dir: Path = FONT_DIR):
        self.dir = font_dir
        self._cache: dict = {}
        cmap = TTFont(str(font_dir / self.FILES["regular"])).getBestCmap()
        self.supported = set(cmap.keys())

    def get(self, style: str, size: int) -> ImageFont.FreeTypeFont:
        key = (style, size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(str(self.dir / self.FILES[style]), size)
        return self._cache[key]

    def fallback_style(self, style: str) -> str:
        return "fb_bold" if style in ("bold", "medium") else "fb_regular"

    def chunks(self, text: str, style: str, size: int):
        """Split text into (substring, font) pieces, using DejaVu for glyphs Poppins lacks (↑ ↓ → ≥ μ ...)."""
        out = []
        cur, cur_fb = "", None
        for ch in text:
            fb = ord(ch) not in self.supported
            if cur and fb != cur_fb:
                out.append((cur, self.get(self.fallback_style(style) if cur_fb else style, size)))
                cur = ""
            cur += ch
            cur_fb = fb
        if cur:
            out.append((cur, self.get(self.fallback_style(style) if cur_fb else style, size)))
        return out


# ------------------------------------------------------------------ rich text layout

def parse_rich(text: str):
    """'**bold** plain' -> [('bold', True), (' plain', False)]"""
    runs = []
    for part in re.split(r"(\*\*[^*]+?\*\*)", text or ""):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append((part[2:-2], True))
        else:
            runs.append((part.replace("**", ""), False))
    return runs


@dataclass
class Line:
    items: list = field(default_factory=list)   # (text, font, is_bold)
    width: float = 0.0


@dataclass
class Block:
    lines: list
    size: int
    cap: float
    lh: float = LH

    @property
    def height(self) -> float:
        if not self.lines:
            return 0
        return self.cap + (len(self.lines) - 1) * self.size * self.lh + 0.30 * self.size


class Typesetter:
    def __init__(self, fonts: Fonts):
        self.f = fonts

    def cap_height(self, size: int) -> float:
        return -self.f.get("bold", size).getbbox("H", anchor="ls")[1]

    def _word_pieces(self, word, style, bold_style, size):
        pieces = []
        for txt, is_bold in word:
            for chunk, font in self.f.chunks(txt, bold_style if is_bold else style, size):
                pieces.append((chunk, font, is_bold))
        return pieces

    def layout(self, text: str, size: int, max_w: float, style="regular", bold_style="bold", lh=LH) -> Block:
        # Build words as lists of (text, bold) so "**30S**," stays one word.
        words, cur = [], []
        for txt, is_bold in parse_rich(text):
            for tok in re.findall(r"\n|[^\S\n]+|[^\s]+", txt):
                if tok == "\n":
                    if cur:
                        words.append(cur)
                        cur = []
                    words.append("\n")
                elif tok.isspace():
                    if cur:
                        words.append(cur)
                        cur = []
                else:
                    cur.append((tok, is_bold))
        if cur:
            words.append(cur)

        space = self.f.get(style, size).getlength(" ")
        lines, line = [], Line()
        for w in words:
            if w == "\n":
                lines.append(line)
                line = Line()
                continue
            pieces = self._word_pieces(w, style, bold_style, size)
            ww = sum(font.getlength(t) for t, font, _ in pieces)
            if ww > max_w:  # very long token: hard-break by characters
                for t, font, b in pieces:
                    for ch in t:
                        cw = font.getlength(ch)
                        if line.width + cw > max_w and line.items:
                            lines.append(line)
                            line = Line()
                        line.items.append((ch, font, b))
                        line.width += cw
                continue
            add = ww + (space if line.items else 0)
            if line.items and line.width + add > max_w:
                lines.append(line)
                line = Line()
                add = ww
            if line.items:
                line.items.append((" ", self.f.get(style, size), False))
            line.items.extend(pieces)
            line.width += add
        if line.items:
            lines.append(line)
        return Block(lines=lines, size=size, cap=self.cap_height(size), lh=lh)

    def draw(self, d: ImageDraw.ImageDraw, block: Block, x: float, y: float, color, bold_color=None,
             align="left", box_w: float | None = None) -> float:
        """Draw block with its visual top at y. Returns the visual bottom."""
        bold_color = bold_color or color
        baseline = y + block.cap
        for line in block.lines:
            if align == "center" and box_w:
                cx = x + (box_w - line.width) / 2
            elif align == "right" and box_w:
                cx = x + box_w - line.width
            else:
                cx = x
            for t, font, is_bold in line.items:
                d.text((cx, baseline), t, font=font, fill=bold_color if is_bold else color, anchor="ls")
                cx += font.getlength(t)
            baseline += block.size * block.lh
        return y + block.height


# ------------------------------------------------------------------ renderer

class SlideRenderer:
    def __init__(self, cfg, fonts: Fonts | None = None):
        self.cfg = cfg
        self.c = cfg["colors"]
        self.fonts = fonts or Fonts()
        self.ts = Typesetter(self.fonts)
        logo = ASSETS_DIR / "logo.png"
        self.logo = Image.open(logo).convert("RGBA") if logo.exists() else None

    # ---- helpers
    def _autosize(self, measure, avail_h, hi, lo, step=2):
        """measure(size) -> (height, payload). Largest size that fits avail_h."""
        for s in range(hi, lo - 1, -step):
            h, payload = measure(s)
            if h <= avail_h:
                return s, payload, False
        h, payload = measure(lo)
        return lo, payload, True

    def _pill(self, d, x, y, text, size, fg, bg=None, outline=None, pad_x=20, pad_y=10, style="medium"):
        font = self.fonts.get(style, size)
        tw = font.getlength(text)
        cap = self.ts.cap_height(size)
        h = cap + 2 * pad_y + 6
        d.rounded_rectangle([x, y, x + tw + 2 * pad_x, y + h], radius=h / 2, fill=bg,
                            outline=outline, width=3 if outline else 0)
        d.text((x + pad_x, y + pad_y + cap + 3), text, font=font, fill=fg, anchor="ls")
        return x + tw + 2 * pad_x, y + h

    def _paste_logo(self, img, x, y, size):
        if self.logo is None:
            return False
        lg = self.logo.copy()
        lg.thumbnail((size, size))
        img.paste(lg, (int(x), int(y)), lg)
        return True

    def _rings(self, d, cx, cy, radii, color):
        for r in radii:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=3)

    def _chrome(self, img, d, idx, total, label, footer_hint):
        c = self.c
        d.rectangle([0, 0, W, H], fill=c["cream"])
        d.rectangle([0, 0, W, 14], fill=c["navy"])
        self._pill(d, M, 58, label.upper(), 24, c["navy"], outline=c["navy"])
        f = self.fonts.get("medium", 26)
        d.text((W - M, 58 + self.ts.cap_height(26) + 13), f"{idx}/{total}", font=f, fill=c["muted"], anchor="rs")
        d.line([M, H - 112, W - M, H - 112], fill=c["line"], width=2)
        fh = self.fonts.get("medium", 26)
        d.text((M, H - 58), self.cfg["handle"], font=fh, fill=c["navy"], anchor="ls")
        if footer_hint:
            x_end = W - M
            for t, font in reversed(self.fonts.chunks(footer_hint, "medium", 26)):
                x_end -= font.getlength(t)
                d.text((x_end, H - 58), t, font=font, fill=c["gold"] if "→" in t else c["muted"], anchor="ls")

    def _heading(self, d, text, y=150):
        c = self.c
        size, block, _ = self._autosize(
            lambda s: ((b := self.ts.layout(text, s, W - 2 * M, style="bold")).height + (0 if len(b.lines) <= 2 else 999), b),
            avail_h=170, hi=60, lo=40)
        bottom = self.ts.draw(d, block, M, y, c["navy"], c["navy"])
        d.rounded_rectangle([M, bottom + 22, M + 84, bottom + 30], radius=4, fill=c["gold"])
        return bottom + 30

    CONTENT_BOTTOM = H - 150

    # ---- slide types
    def cover(self, img, d, s, idx, total, post):
        c = self.c
        d.rectangle([0, 0, W, H], fill=c["navy"])
        self._rings(d, W - 40, 120, (210, 300, 390), c["navy_soft"])
        has_logo = self._paste_logo(img, M, 70, 120)
        fb = self.fonts.get("medium", 26)
        d.text((M + (140 if has_logo else 0), 70 + 26), self.cfg["brand_name"].upper(), font=fb, fill=c["gold"], anchor="ls")
        d.text((W - M, 70 + 26), f"{idx}/{total}", font=fb, fill="#9FB0C9", anchor="rs")

        avail_w = W - 2 * M
        region_top, region_bottom = 250, H - 210
        pill_h = self.ts.cap_height(28) + 26
        sub_size, sub_block, ov1 = self._autosize(
            lambda z: ((b := self.ts.layout(s["subtitle"], z, avail_w, style="regular")).height, b), 230, 42, 28)
        avail_h = (region_bottom - region_top) - pill_h - 56 - sub_block.height - 46
        size, block, ov2 = self._autosize(
            lambda z: ((b := self.ts.layout(s["headline"], z, avail_w, style="bold", lh=1.14)).height, b),
            avail_h, 112, 56, step=4)
        group = pill_h + 56 + block.height + 46 + sub_block.height
        top = region_top + max(0, (region_bottom - region_top - group) * 0.42)
        _, pill_bottom = self._pill(d, M, top, s["kicker"].upper(), 28, c["navy"], bg=c["gold"], style="bold")
        bottom = self.ts.draw(d, block, M, pill_bottom + 56, "#FFFFFF", c["gold"])
        self.ts.draw(d, sub_block, M, bottom + 46, "#C9D3E3", "#FFFFFF")

        d.line([M, H - 150, W - M, H - 150], fill=c["navy_soft"], width=2)
        fh = self.fonts.get("medium", 28)
        d.text((M, H - 90), self.cfg["handle"], font=fh, fill="#FFFFFF", anchor="ls")
        x = W - M
        for t, font in reversed(self.fonts.chunks("Swipe  →", "bold", 30)):
            x -= font.getlength(t)
            d.text((x, H - 90), t, font=font, fill=c["gold"], anchor="ls")
        return ov1 or ov2

    def bullets(self, img, d, s, idx, total, post):
        c = self.c
        self._chrome(img, d, idx, total, post["_label"], "swipe →")
        top = self._heading(d, s["heading"]) + 50
        indent = 44
        avail_w = W - 2 * M - indent

        def measure(z):
            blocks = [self.ts.layout(b, z, avail_w) for b in s["bullets"]]
            gap = z * 0.75
            return sum(b.height for b in blocks) + gap * (len(blocks) - 1), blocks

        size, blocks, ov = self._autosize(measure, self.CONTENT_BOTTOM - top, 46, 30)
        y = top
        for b in blocks:
            sq = size * 0.36
            cy = y + b.cap / 2
            d.rounded_rectangle([M, cy - sq / 2, M + sq, cy + sq / 2], radius=3, fill=c["gold"])
            y = self.ts.draw(d, b, M + indent, y, c["ink"], c["navy"]) + size * 0.75
        return ov

    def table(self, img, d, s, idx, total, post):
        c = self.c
        self._chrome(img, d, idx, total, post["_label"], "swipe →")
        top = self._heading(d, s["heading"]) + 46
        cols, rows = s["columns"], s["rows"]
        n = len(cols)
        total_w = W - 2 * M

        def strip(x):
            return re.sub(r"\*\*", "", x)

        # Weight columns by how much text they hold; every column is at least as wide as its longest word.
        weights = [max(sum(len(strip(r[j])) for r in rows) + len(strip(cols[j])), 10) ** 0.85 for j in range(n)]
        px = 18

        def col_widths(z):
            mins = []
            for j in range(n):
                longest = 0.0
                for k, t in enumerate([cols[j]] + [r[j] for r in rows]):
                    style = "bold" if k == 0 else ("medium" if j == 0 else "regular")
                    for word in strip(t).split():
                        wpx = sum(f.getlength(ch) for ch, f in self.fonts.chunks(word, style, z))
                        longest = max(longest, wpx)
                mins.append(longest + 2 * px + 4)
            spare = total_w - sum(mins)
            if spare < 0:
                return None
            # Hand the spare width out by weight, but never shrink a column below its minimum.
            ideal = [total_w * w / sum(weights) for w in weights]
            widths = [max(m, i) for m, i in zip(mins, ideal)]
            excess = sum(widths) - total_w
            while excess > 0.5:
                flex = [j for j in range(n) if widths[j] > mins[j] + 0.5]
                if not flex:
                    return None
                cut = excess / len(flex)
                for j in flex:
                    take = min(cut, widths[j] - mins[j])
                    widths[j] -= take
                excess = sum(widths) - total_w
            return widths

        def measure(z):
            widths = col_widths(z)
            if widths is None:
                return 10 ** 6, None
            py = z * 0.55
            header = [self.ts.layout(t, z, widths[j] - 2 * px, style="bold") for j, t in enumerate(cols)]
            hh = max(b.height for b in header) + 2 * py
            body = []
            for r in rows:
                cells = [self.ts.layout(t, z, widths[j] - 2 * px, style="medium" if j == 0 else "regular",
                                        bold_style="bold") for j, t in enumerate(r)]
                body.append((cells, max(b.height for b in cells) + 2 * py))
            return hh + sum(h for _, h in body), (header, hh, body, py, widths)

        size, payload, ov = self._autosize(measure, self.CONTENT_BOTTOM - top, 38, 24)
        if payload is None:  # cannot fit even at minimum size: equal columns, hard-wrapped
            ov = True
            widths = [total_w / n] * n
            py = size * 0.55
            header = [self.ts.layout(t, size, widths[j] - 2 * px, style="bold") for j, t in enumerate(cols)]
            hh = max(b.height for b in header) + 2 * py
            body = [([self.ts.layout(t, size, widths[j] - 2 * px) for j, t in enumerate(r)], 0) for r in rows]
            body = [(cells, max(b.height for b in cells) + 2 * py) for cells, _ in body]
        else:
            header, hh, body, py, widths = payload
        tw = total_w
        table_h = hh + sum(h for _, h in body)
        d.rounded_rectangle([M, top, M + tw, top + table_h], radius=22, fill="#FFFFFF", outline=c["line"], width=2)
        d.rounded_rectangle([M, top, M + tw, top + hh], radius=22, fill=c["navy"])
        d.rectangle([M, top + hh - 24, M + tw, top + hh], fill=c["navy"])
        x = M
        for j, b in enumerate(header):
            self.ts.draw(d, b, x + px, top + py, "#FFFFFF", c["gold"])
            x += widths[j]
        y = top + hh
        for r_i, (cells, rh) in enumerate(body):
            if r_i % 2 == 1:
                bottom_round = r_i == len(body) - 1
                if bottom_round:
                    d.rounded_rectangle([M + 2, y, M + tw - 2, y + rh - 2], radius=20, fill=c["row_alt"])
                    d.rectangle([M + 2, y, M + tw - 2, y + rh - 24], fill=c["row_alt"])
                else:
                    d.rectangle([M + 2, y, M + tw - 2, y + rh], fill=c["row_alt"])
            x = M
            for j, b in enumerate(cells):
                self.ts.draw(d, b, x + px, y + py, c["navy"] if j == 0 else c["ink"], c["navy"])
                x += widths[j]
            if r_i < len(body) - 1:
                d.line([M + 2, y + rh, M + tw - 2, y + rh], fill=c["line"], width=2)
            y += rh
        x = M
        for j in range(n - 1):
            x += widths[j]
            d.line([x, top + hh, x, top + table_h - 2], fill=c["line"], width=2)
        return ov

    def mnemonic(self, img, d, s, idx, total, post):
        c = self.c
        self._chrome(img, d, idx, total, post["_label"], "swipe →")
        top = self._heading(d, s["heading"]) + 44
        panel_w = W - 2 * M
        msize, mblock, ov1 = self._autosize(
            lambda z: ((b := self.ts.layout(s["mnemonic"], z, panel_w - 80, style="bold", lh=1.15)).height
                       + (0 if len(b.lines) <= 2 else 999), b), 190, 66, 36)
        ph = mblock.height + 70
        d.rounded_rectangle([M, top, M + panel_w, top + ph], radius=26, fill=c["navy"])
        self.ts.draw(d, mblock, M + 40, top + 35, c["gold"], c["gold"], align="center", box_w=panel_w - 80)
        y0 = top + ph + 46
        items = s["items"]

        def measure(z):
            circle = z * 1.75
            text_w = panel_w - circle - 30
            blocks = [self.ts.layout(it["meaning"], z, text_w) for it in items]
            hts = [max(b.height, circle) for b in blocks]
            return sum(hts) + z * 0.55 * (len(items) - 1), (blocks, hts, circle)

        size, (blocks, hts, circle), ov2 = self._autosize(measure, self.CONTENT_BOTTOM - y0, 44, 28)
        y = y0
        for it, b, h in zip(items, blocks, hts):
            d.ellipse([M, y, M + circle, y + circle], fill=c["gold"])
            letter = re.sub(r"\*\*", "", it["letter"])
            lsize = int(circle * (0.5 if len(letter) == 1 else 0.36))
            lf = self.fonts.get("bold", lsize)
            d.text((M + circle / 2, y + circle / 2), letter, font=lf, fill=c["navy"], anchor="mm")
            ty = y + max(0, (circle - b.height) / 2)
            self.ts.draw(d, b, M + circle + 30, ty, c["ink"], c["navy"])
            y += h + size * 0.55
        return ov1 or ov2

    def question(self, img, d, s, idx, total, post):
        c = self.c
        self._chrome(img, d, idx, total, post["_label"], "answer on next slide →")
        _, pb = self._pill(d, M, 150, "PRACTICE QUESTION", 26, "#FFFFFF", bg=c["coral"], style="bold")
        top = pb + 40
        avail_w = W - 2 * M
        opts = s["options"]

        def measure(z):
            stem = self.ts.layout(s["stem"], z, avail_w)
            circle = z * 1.45
            ob = [self.ts.layout(o, z * 0.94, avail_w - circle - 64, style="medium") for o in opts]
            oh = [max(b.height, circle) + z * 0.7 for b in ob]
            gap_stem = z * 1.1
            return stem.height + gap_stem + sum(oh) + z * 0.4 * (len(opts) - 1), (stem, ob, oh, circle, gap_stem)

        size, (stem, ob, oh, circle, gap), ov = self._autosize(measure, self.CONTENT_BOTTOM - top, 38, 27)
        y = self.ts.draw(d, stem, M, top, c["ink"], c["navy"]) + gap
        for k, (b, h) in enumerate(zip(ob, oh)):
            d.rounded_rectangle([M, y, W - M, y + h], radius=18, fill="#FFFFFF", outline=c["line"], width=2)
            cy = y + h / 2
            d.ellipse([M + 18, cy - circle / 2, M + 18 + circle, cy + circle / 2], fill=c["navy"])
            d.text((M + 18 + circle / 2, cy), "ABCDE"[k], font=self.fonts.get("bold", int(circle * 0.5)),
                   fill="#FFFFFF", anchor="mm")
            self.ts.draw(d, b, M + circle + 40, cy - b.height / 2, c["ink"], c["navy"])
            y += h + size * 0.4
        return ov

    def answer(self, img, d, s, idx, total, post):
        c = self.c
        self._chrome(img, d, idx, total, post["_label"], "swipe →")
        top = self._heading(d, "Answer & explanation") + 46
        avail_w = W - 2 * M
        letter = str(s["correct"]).strip().upper()[:1]
        dis = s.get("distractors") or []

        def measure(z):
            badge = z * 2.1
            ans = self.ts.layout(s["answer_text"], z * 1.05, avail_w - badge - 90, style="bold")
            panel_h = max(ans.height, badge) + z * 1.2
            exp = self.ts.layout(s["explanation"], z, avail_w)
            dblocks = [self.ts.layout(x, z * 0.86, avail_w - 34, style="regular") for x in dis]
            hdr = z * 1.2 if dis else 0
            total_h = panel_h + z * 1.0 + exp.height + (z * 1.0 + hdr + sum(b.height + z * 0.4 for b in dblocks) if dis else 0)
            return total_h, (badge, ans, panel_h, exp, dblocks)

        size, (badge, ans, ph, exp, dblocks), ov = self._autosize(measure, self.CONTENT_BOTTOM - top, 38, 27)
        d.rounded_rectangle([M, top, W - M, top + ph], radius=24, fill=c["mint"])
        cy = top + ph / 2
        d.ellipse([M + 24, cy - badge / 2, M + 24 + badge, cy + badge / 2], fill="#FFFFFF")
        d.text((M + 24 + badge / 2, cy), letter, font=self.fonts.get("bold", int(badge * 0.55)), fill=c["mint"], anchor="mm")
        self.ts.draw(d, ans, M + badge + 60, cy - ans.height / 2, "#FFFFFF", "#FFFFFF")
        y = top + ph + size * 1.0
        y = self.ts.draw(d, exp, M, y, c["ink"], c["navy"])
        if dblocks:
            y += size * 1.0
            hf = self.fonts.get("bold", int(size * 0.8))
            d.text((M, y + self.ts.cap_height(int(size * 0.8))), "WHY NOT THE OTHERS", font=hf, fill=c["muted"], anchor="ls")
            y += size * 1.2
            for b in dblocks:
                d.rectangle([M, y + 4, M + 6, y + b.height - 2], fill=c["line"])
                y = self.ts.draw(d, b, M + 34, y, c["muted"], c["ink"]) + size * 0.4
        return ov

    def pearl(self, img, d, s, idx, total, post):
        c = self.c
        self._chrome(img, d, idx, total, post["_label"], "swipe →")
        top = self._heading(d, s["heading"]) + 50
        trap = s.get("variant") == "trap"
        bg = c["coral"] if trap else c["mint"]
        label = "NBME TRAP" if trap else "HIGH-YIELD PEARL"
        pw = W - 2 * M
        pad = 56
        avail = self.CONTENT_BOTTOM - top

        def measure(z):
            tb = self.ts.layout(s["text"], z, pw - 2 * pad, style="bold", lh=1.24)
            db = self.ts.layout(s["detail"], int(z * 0.62), pw - 2 * pad) if s.get("detail") else None
            h = pad + 40 + 40 + tb.height + ((40 + db.height) if db else 0) + pad
            return h, (tb, db)

        size, (tb, db), ov = self._autosize(measure, avail, 54, 34)
        ph = measure(size)[0]
        y0 = top + max(0, (avail - ph) / 2 - 20)
        d.rounded_rectangle([M, y0, M + pw, y0 + ph], radius=30, fill=bg)
        lf = self.fonts.get("bold", 26)
        d.text((M + pad, y0 + pad + self.ts.cap_height(26)), label, font=lf, fill="#FFFFFF", anchor="ls")
        y = y0 + pad + 40 + 40
        y = self.ts.draw(d, tb, M + pad, y, "#FFFFFF", "#FFFFFF")
        if db:
            d.line([M + pad, y + 20, M + pad + 120, y + 20], fill="#FFFFFF", width=3)
            self.ts.draw(d, db, M + pad, y + 40, "#FFFFFF", "#FFFFFF")
        return ov

    def cta(self, img, d, s, idx, total, post):
        c = self.c
        d.rectangle([0, 0, W, H], fill=c["navy"])
        self._rings(d, 40, H - 120, (200, 290, 380), c["navy_soft"])
        has_logo = self._paste_logo(img, M, 70, 120)
        fb = self.fonts.get("medium", 26)
        d.text((M + (140 if has_logo else 0), 96), self.cfg["brand_name"].upper(), font=fb, fill=c["gold"], anchor="ls")
        d.text((W - M, 96), f"{idx}/{total}", font=fb, fill="#9FB0C9", anchor="rs")
        y = 300
        hb = self.ts.layout("Found this **useful?**", 76, W - 2 * M, style="bold", lh=1.15)
        y = self.ts.draw(d, hb, M, y, "#FFFFFF", c["gold"]) + 80
        rows = [
            ("01", "**Save** this post for your revision"),
            ("02", "**Share** it with your Step 1 study partner"),
            ("03", f"**Follow** {self.cfg['handle']}  for daily high-yield"),
        ]
        for num, text in rows:
            nf = self.fonts.get("bold", 34)
            d.text((M, y + self.ts.cap_height(34)), num, font=nf, fill=c["gold"], anchor="ls")
            b = self.ts.layout(text, 38, W - 2 * M - 90, style="regular", bold_style="bold")
            yy = self.ts.draw(d, b, M + 90, y, "#DCE4F0", "#FFFFFF")
            y = yy + 52
        y += 30
        pb = self.ts.layout(self.cfg["cta_mentoring_line"], 34, W - 2 * M - 80, style="bold")
        ph = pb.height + 64
        d.rounded_rectangle([M, y, W - M, y + ph], radius=ph / 2 if len(pb.lines) == 1 else 30, fill=c["gold"])
        self.ts.draw(d, pb, M + 40, y + 32, c["navy"], c["navy"], align="center", box_w=W - 2 * M - 80)
        fh = self.fonts.get("medium", 28)
        d.text((M, H - 90), self.cfg["handle"], font=fh, fill="#FFFFFF", anchor="ls")
        return False

    # ---- public
    def render_post(self, post: dict, out_dir: Path, label: str) -> tuple[list[Path], list[int]]:
        """Render all slides. Returns (jpeg paths, 1-based indices of slides whose text overflowed)."""
        out_dir.mkdir(parents=True, exist_ok=True)
        post = {**post, "_label": label}
        slides = post["slides"]
        paths, overflow = [], []
        for i, s in enumerate(slides, 1):
            img = Image.new("RGB", (W, H), self.c["cream"])
            d = ImageDraw.Draw(img)
            fn = getattr(self, s["type"])
            if fn(img, d, s, i, len(slides), post):
                overflow.append(i)
            p = out_dir / f"slide_{i:02d}.jpg"
            img.save(p, "JPEG", quality=92, optimize=True, progressive=False, subsampling=0)
            paths.append(p)
        return paths, overflow

    def render_story(self, post: dict, out_path: Path, series: str) -> Path:
        c = self.c
        img = Image.new("RGB", (SW, SH), c["navy"])
        d = ImageDraw.Draw(img)
        self._rings(d, SW - 60, 300, (220, 320, 420), c["navy_soft"])
        self._rings(d, 60, SH - 260, (180, 270), c["navy_soft"])
        story = post["story"]
        y = 360
        _, y = self._pill(d, M, y, "NEW POST", 32, c["navy"], bg=c["gold"], style="bold")
        f = self.fonts.get("medium", 34)
        d.text((M, y + 70), series.upper(), font=f, fill="#9FB0C9", anchor="ls")
        y += 120
        size, block, _ = self._autosize(
            lambda z: ((b := self.ts.layout(story["headline"], z, SW - 2 * M, style="bold", lh=1.14)).height, b),
            560, 108, 56, step=4)
        y = self.ts.draw(d, block, M, y, "#FFFFFF", c["gold"]) + 80
        tb = self.ts.layout(story["teaser"], 44, SW - 2 * M - 100, style="medium")
        ph = tb.height + 100
        d.rounded_rectangle([M, y, SW - M, y + ph], radius=34, fill="#FFFFFF")
        self.ts.draw(d, tb, M + 50, y + 50, c["navy"], c["navy"])
        y += ph + 70
        x = M
        for t, font in self.fonts.chunks("Today's post is on our profile  →", "bold", 40):
            d.text((x, y + 40), t, font=font, fill=c["gold"], anchor="ls")
            x += font.getlength(t)
        fh = self.fonts.get("medium", 34)
        d.text((SW / 2, SH - 300), self.cfg["handle"], font=fh, fill="#FFFFFF", anchor="ms")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "JPEG", quality=92, optimize=True, subsampling=0)
        return out_path
