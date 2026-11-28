"""Pictures shown over the character: captions, what it heard, the microphone-off sign, hints while the
button is held or an alarm rings, and the clock with the date and the weather (docs/28-user-guide.md).

The stage draws them (stage/src/overlay.rs). The core makes each one as a PNG, sized for the screen the
stage reports, in the overlay folder beside the stage's socket: /run/companion/overlays on the device,
which is in memory, so captions never wear the card. Then it names the picture in a command.
"""
import asyncio
import io
import itertools
import math
import os

from PIL import Image, ImageDraw, ImageFont

from .store import write_atomic

# Slots, and where each sits on the screen.
CAPTION, HEARD, MIC, HINT, CLOCK = "caption", "heard", "mic-off", "hint", "clock"
ANCHORS = {CAPTION: "bottom", HEARD: "top", MIC: "top_right", HINT: "center", CLOCK: "top_left"}
LINES_PER_PAGE = 3
CHARS_PER_SECOND = 14.0       # how fast the device's voices speak, roughly, at speed 1
CAPTION_LINGER = 3.0          # seconds a caption stays after the character stops talking
FONT_SHARE = {"normal": 0.058, "large": 0.075}    # of the screen's shorter side


def _font(size, bold=True):
    names = ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf") if bold else ("DejaVuSans.ttf",)
    for folder in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF", "/usr/share/fonts/dejavu"):
        for name in names:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                 # an old Pillow without sizes
        return ImageFont.load_default()


def wrap(text, font, width):
    """Lines of `text` no wider than `width` pixels (a word too long for a line is broken)."""
    measure = ImageDraw.Draw(Image.new("L", (1, 1))).textlength
    lines, line = [], ""
    for word in text.split():
        while measure(word, font=font) > width and len(word) > 1:
            cut = len(word)
            while cut > 1 and measure(word[:cut] + "-", font=font) > width:
                cut -= 1
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:cut] + "-")
            word = word[cut:]
        trial = (line + " " + word).strip()
        if not line or measure(trial, font=font) <= width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _box(lines, font, size, box_width, fill, text_fill=(246, 246, 248, 255)):
    """A rounded box of centred lines, as a PNG."""
    pad_x, pad_y = round(size * 0.6), round(size * 0.42)
    line_height = round(size * 1.22)
    height = 2 * pad_y + line_height * len(lines)
    scale = 2                                    # drawn large, then shrunk: smooth corners
    image = Image.new("RGBA", (box_width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, box_width * scale - 1, height * scale - 1), radius=round(size * 0.45) * scale, fill=fill)
    image = image.resize((box_width, height), Image.LANCZOS)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        y = pad_y + i * line_height + round((line_height - size) * 0.35)
        draw.text(((box_width - w) / 2, y), line, font=font, fill=text_fill)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def caption_pages(text, screen=(720, 1280), size="normal"):
    """A sentence as one or more caption pictures (at most three lines each), and the share of the
    sentence each page holds (for timing them against the voice)."""
    side = min(screen)
    font_size = max(16, round(side * FONT_SHARE.get(size, FONT_SHARE["normal"])))
    font = _font(font_size)
    margin = round(side * 0.03)
    box_width = screen[0] - 2 * margin
    lines = wrap(" ".join(text.split()), font, box_width - 2 * round(font_size * 0.6)) or [""]
    pages = [lines[i:i + LINES_PER_PAGE] for i in range(0, len(lines), LINES_PER_PAGE)]
    total = sum(len(l) for l in lines) or 1
    shares = [sum(len(l) for l in page) / total for page in pages]
    return [_box(page, font, font_size, box_width, (14, 14, 20, 205)) for page in pages], shares


def heard_png(text, screen=(720, 1280)):
    """What the person said, smaller, at the top: two lines at most (the start is kept)."""
    side = min(screen)
    font_size = max(14, round(side * 0.042))
    font = _font(font_size, bold=False)
    margin = round(side * 0.03)
    # Narrower than the screen by the microphone-off sign on each side, so the two never overlap.
    sign = max(48, round(side * 0.13))
    box_width = screen[0] - 2 * (2 * margin + sign)
    lines = wrap("“%s”" % " ".join(text.split()), font, box_width - 2 * round(font_size * 0.6))
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1].rstrip("”") + "…”"
    return _box(lines, font, font_size, box_width, (44, 48, 70, 200), text_fill=(225, 228, 240, 255))


def hint_png(text, screen=(720, 1280)):
    side = min(screen)
    font_size = max(16, round(side * 0.05))
    font = _font(font_size)
    box_width = round(screen[0] * 0.86)
    lines = wrap(text, font, box_width - 2 * round(font_size * 0.6))
    return _box(lines, font, font_size, box_width, (20, 22, 34, 225))


def mic_off_png(screen=(720, 1280)):
    """The microphone-off sign: a microphone, struck through, on a dark disc."""
    side = max(48, round(min(screen) * 0.13))
    s = 4                                        # drawn four times larger, then shrunk
    big = side * s
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, big - 1, big - 1), fill=(16, 16, 22, 215))
    white, red = (240, 240, 244, 255), (235, 72, 72, 255)
    c = big / 2
    w = big * 0.07
    head = (c - big * 0.11, big * 0.2, c + big * 0.11, big * 0.56)
    draw.rounded_rectangle(head, radius=big * 0.11, fill=white)
    draw.arc((c - big * 0.2, big * 0.3, c + big * 0.2, big * 0.66), start=0, end=180, fill=white, width=round(w * 0.7))
    draw.line((c, big * 0.66, c, big * 0.77), fill=white, width=round(w * 0.7))
    draw.line((c - big * 0.12, big * 0.78, c + big * 0.12, big * 0.78), fill=white, width=round(w * 0.7))
    # The strike: a dark edge, then red, so it reads on the white microphone.
    a, b = (big * 0.24, big * 0.22), (big * 0.76, big * 0.8)
    draw.line((a, b), fill=(16, 16, 22, 255), width=round(w * 1.9))
    draw.line((a, b), fill=red, width=round(w))
    image = image.resize((side, side), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


SUN, MOON = (255, 210, 90, 255), (232, 230, 242, 255)
CLOUD, DARK_CLOUD = (236, 238, 244, 255), (176, 182, 198, 255)
RAIN, BOLT = (120, 178, 255, 255), (255, 214, 64, 255)


def weather_icon(kind, size):
    """A small picture of the weather, drawn here (a sun or moon, clouds, rain, snow, lightning, fog), as RGBA.
    `kind` is weather.symbol's picture: clear, fair, partly (each with -night), cloud, rain, snow, thunder, fog."""
    s = 4
    big = size * s
    layers = []

    def layer():
        im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        layers.append(im)
        return ImageDraw.Draw(im)

    def sun(cx, cy, r):
        d = layer()
        for k in range(8):
            a = math.pi * k / 4
            d.line([(cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35),
                    (cx + math.cos(a) * r * 1.8, cy + math.sin(a) * r * 1.8)], fill=SUN, width=max(2, round(r * 0.22)))
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=SUN)

    def moon(cx, cy, r):
        d = layer()
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=MOON)
        d.ellipse((cx - r * 0.35, cy - r * 1.15, cx + r * 1.65, cy + r * 0.85), fill=(0, 0, 0, 0))   # the crescent

    def cloud(cx, cy, w, colour):
        d = layer()
        h = w * 0.5
        d.rounded_rectangle((cx - w / 2, cy - h * 0.15, cx + w / 2, cy + h / 2), radius=h * 0.33, fill=colour)
        for dx, dy, r in ((-0.22, 0.02, 0.21), (0.06, -0.12, 0.29), (0.3, 0.06, 0.18)):
            d.ellipse((cx + dx * w - r * w, cy + dy * w - r * w, cx + dx * w + r * w, cy + dy * w + r * w), fill=colour)

    night = kind.endswith("-night")
    base = kind[:-6] if night else kind
    light = moon if night else sun
    if base == "clear":
        light(big * 0.5, big * 0.5, big * (0.3 if night else 0.24))
    elif base in ("fair", "partly"):
        light(big * 0.42, big * 0.4, big * (0.25 if night else 0.2))
        cloud(big * 0.6, big * 0.64, big * (0.5 if base == "fair" else 0.66), CLOUD)
    elif base == "fog":
        d = layer()
        for k, y in enumerate((0.36, 0.52, 0.68)):
            inset = 0.12 if k % 2 else 0.2
            d.rounded_rectangle((big * inset, big * y - big * 0.045, big * (1 - inset), big * y + big * 0.045),
                                radius=big * 0.045, fill=DARK_CLOUD)
    else:
        wet = base in ("rain", "snow", "thunder")
        cloud(big * 0.5, big * (0.38 if wet else 0.5), big * 0.8, DARK_CLOUD if wet else CLOUD)
        d = layer()
        if base == "rain":
            for x in (0.3, 0.5, 0.7):
                d.line([(big * x, big * 0.7), (big * (x - 0.06), big * 0.88)], fill=RAIN, width=round(big * 0.07))
        elif base == "snow":
            for x, y in ((0.3, 0.74), (0.5, 0.86), (0.7, 0.74)):
                r = big * 0.055
                d.ellipse((big * x - r, big * y - r, big * x + r, big * y + r), fill=(250, 250, 255, 255))
        elif base == "thunder":
            d.polygon([(big * 0.54, big * 0.6), (big * 0.4, big * 0.8), (big * 0.5, big * 0.8), (big * 0.44, big * 0.97),
                       (big * 0.64, big * 0.72), (big * 0.53, big * 0.72), (big * 0.6, big * 0.6)], fill=BOLT)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    for im in layers:
        out = Image.alpha_composite(out, im)
    return out.resize((size, size), Image.LANCZOS)


def clock_png(shown_time, suffix="", date="", glance=None, screen=(720, 1280)):
    """The clock: the time large, with "am" or "pm"; under it the date and, with `glance` (picture, degrees),
    the weather. A see-through dark box behind, so it reads over any background."""
    side = min(screen)
    big_size = max(28, round(side * 0.115))
    small_size = max(14, round(side * 0.045))
    big, small, little = _font(big_size), _font(small_size, bold=False), _font(max(12, round(big_size * 0.3)))
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    _, top, _, _ = big.getbbox(shown_time, anchor="ls")
    gap = round(big_size * 0.08)
    time_width = measure.textlength(shown_time, font=big) + (gap + measure.textlength(suffix, font=little) if suffix else 0)
    icon = round(small_size * 1.45)
    degrees = ("%d°" % glance[1]).replace("-", "\u2212") if glance else ""
    line_width = measure.textlength(date, font=small)
    if glance:
        line_width += round(small_size * 0.7) + icon + round(small_size * 0.2) + measure.textlength(degrees, font=small)
    pad_x, pad_y = round(small_size * 0.8), round(small_size * 0.55)
    width = round(max(time_width, line_width)) + 2 * pad_x
    baseline = pad_y - top
    second = baseline + round(small_size * 0.6)               # the top of the second line
    height = second + max(icon, round(small_size * 1.25)) + pad_y
    scale = 2
    image = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((0, 0, width * scale - 1, height * scale - 1),
                                            radius=round(small_size * 0.7) * scale, fill=(14, 14, 20, 150))
    image = image.resize((width, height), Image.LANCZOS)
    draw = ImageDraw.Draw(image)
    white = (248, 248, 250, 255)
    draw.text((pad_x, baseline), shown_time, font=big, fill=white, anchor="ls")
    if suffix:
        draw.text((pad_x + measure.textlength(shown_time, font=big) + gap, baseline), suffix, font=little,
                  fill=(214, 216, 226, 255), anchor="ls")
    middle = second + max(icon, round(small_size * 1.25)) / 2
    draw.text((pad_x, middle), date, font=small, fill=(226, 228, 236, 255), anchor="lm")
    if glance:
        x = pad_x + measure.textlength(date, font=small) + round(small_size * 0.7)
        picture = weather_icon(glance[0], icon)
        image.alpha_composite(picture, (round(x), round(middle - icon / 2)))
        draw.text((x + icon + round(small_size * 0.2), middle), degrees, font=small, fill=(226, 228, 236, 255), anchor="lm")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class Overlays:
    """What shows over the character, through the stage."""

    def __init__(self, folder, stage):
        self.folder = folder
        self.stage = stage
        self.screen = (720, 1280)          # updated from the stage's ready and size events
        self._names = itertools.count(1)
        self.shown = {}                    # slot: the picture's name
        self._kept = {}                    # slot: names written, the latest last
        self._pages = None                 # the caption's later pages, waiting their turn
        self._pages_end = 0.0              # when the last of them shows (the event loop's time)
        self._hide = {}                    # slot: a pending hide

    def show(self, slot, png, keep_hide=False):
        """Show a picture in a slot. A pending hide of the slot is cancelled, unless `keep_hide`."""
        os.makedirs(self.folder, exist_ok=True)
        name = "%s-%d.png" % (slot, next(self._names))
        write_atomic(os.path.join(self.folder, name), png)
        if not keep_hide:
            self._cancel_hide(slot)
        self.shown[slot] = name
        sent = self.stage.send({"overlay": {"slot": slot, "image": name, "anchor": ANCHORS.get(slot, "center")}})
        # The stage reads a picture when it takes the command; the two latest are kept for it.
        kept = self._kept.setdefault(slot, [])
        kept.append(name)
        while len(kept) > 2:
            try:
                os.remove(os.path.join(self.folder, kept.pop(0)))
            except OSError:
                pass
        return sent

    def hide(self, slot, after=0.0):
        """Fade a slot out, now or after a while (a later show cancels it)."""
        self._cancel_hide(slot)
        if after > 0:
            try:
                self._hide[slot] = asyncio.get_running_loop().call_later(after, self.hide, slot)
                return
            except RuntimeError:
                pass
        if slot == CAPTION:
            self._stop_pages()
        if self.shown.pop(slot, None) is not None:
            self.stage.send({"overlay": {"slot": slot, "image": None}})

    def clear(self):
        for slot in list(self.shown):
            self.hide(slot)

    def _cancel_hide(self, slot):
        pending = self._hide.pop(slot, None)
        if pending is not None:
            pending.cancel()

    def _stop_pages(self):
        if self._pages is not None:
            for handle in self._pages:
                handle.cancel()
            self._pages = None

    # ----- captions
    def caption(self, text, size="normal", speed=1.0):
        """Show a sentence as the character starts saying it; a long one turns its pages as it goes."""
        self._stop_pages()
        pages, shares = caption_pages(text, self.screen, size)
        self.show(CAPTION, pages[0])
        if len(pages) > 1:
            seconds = len(text) / CHARS_PER_SECOND / max(0.5, float(speed or 1.0))
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            at, handles = 0.0, []
            for page, share in zip(pages[1:], shares[:-1]):
                at += share * seconds
                handles.append(loop.call_later(at, self.show, CAPTION, page, True))
            self._pages = handles
            self._pages_end = loop.time() + at

    def caption_done(self, linger=CAPTION_LINGER):
        """The character stopped talking: the caption, and what it heard, go after a moment."""
        if CAPTION in self.shown:
            later = 0.0
            if self._pages:
                try:
                    later = max(0.0, self._pages_end - asyncio.get_running_loop().time())
                except RuntimeError:
                    pass
            self.hide(CAPTION, after=linger + later)
        if HEARD in self.shown:
            self.hide(HEARD, after=linger)

    def heard(self, text):
        self.show(HEARD, heard_png(text, self.screen))

    def hint(self, text, seconds=0.0):
        self.show(HINT, hint_png(text, self.screen))
        if seconds:
            self.hide(HINT, after=seconds)

    def microphone(self, muted):
        if muted:
            self.show(MIC, mic_off_png(self.screen))
        else:
            self.hide(MIC)

    def clock(self, shown_time, suffix="", date="", glance=None):
        self.show(CLOCK, clock_png(shown_time, suffix, date, glance, self.screen))
