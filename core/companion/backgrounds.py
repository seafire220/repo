"""Backgrounds: a picture behind the character (docs/28-user-guide.md, "Backgrounds").

The built-in backgrounds are drawn here, in code (soft gradients, lights, stars, clouds, a simple room:
nothing copied from anywhere), the first time each is wanted, and kept in DATA/backgrounds/builtin. The
owner can add pictures of their own: each is turned upright, made no larger than the screen needs (1920
pixels on its long side), stripped of the details a camera stores with a photo (where it was taken among
them) and kept as a JPEG in DATA/backgrounds.

One choice is the device's default (the settings' "background"); a character can have its own instead
(its device settings). "auto" follows the time of day: a soft morning, a clear sky, a sunset, a starry
night. The stage draws the chosen picture behind the character (stage/src/background.rs): the core puts
it in the stage settings, and changes it at once with the background command.
"""
import io
import json
import math
import os
import re
import secrets
import time

from .store import write_atomic

# The built-in pictures, in the order the settings page shows them.
BUILTIN = {
    "studio": "Studio",
    "dawn": "Soft morning",
    "day": "Clear sky",
    "sunset": "Sunset",
    "night": "Starry night",
    "room": "Cosy room",
    "blossom": "Blossom",
    "paper": "Paper",
}
# The time of day's picture, for "auto" (parts of the day as memory.part_of_day names them).
AUTO = {"morning": "dawn", "afternoon": "day", "evening": "sunset", "night": "night"}
SPECIAL = {"auto": "Changes with the time of day", "none": "None (a plain dark colour)"}
SIZE = (1080, 1920)
LONGEST = 1920
MAX_BYTES = 20 << 20
MAX_PIXELS = 60_000_000
MAX_PICTURES = 40
SMALLEST = 200
THUMB = (120, 213)
ID = re.compile(r"[a-z0-9_]{1,48}")


class BackgroundError(ValueError):
    pass


def part_of_day(hour):
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening" if 17 <= hour < 21 else "night"


# ---------------------------------------------------------------- the built-in pictures, drawn in code

def _grad(h, w, stops):
    """A top-to-bottom gradient through (share of the height, (r, g, b)) stops."""
    import numpy as np
    t = np.linspace(0.0, 1.0, h)[:, None]
    out = np.zeros((h, 1, 3))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        u = np.clip((t - t0) / max(t1 - t0, 1e-6), 0.0, 1.0)
        inside = (t >= t0) & (t <= t1)
        out = np.where(inside[..., None], np.array(c0) + (np.array(c1) - np.array(c0)) * u[..., None], out)
    return np.repeat(out, w, axis=1)


def _noise(h, w, rng, cells=6, octaves=5, stretch=(1.0, 1.0)):
    """Soft cloudy noise, 0..1: random grids, each finer and fainter, smoothly enlarged and added up."""
    import numpy as np
    from PIL import Image
    total = np.zeros((h, w))
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        gh = max(2, int(cells * stretch[0] * 2 ** o))
        gw = max(2, int(cells * stretch[1] * 2 ** o))
        grid = Image.fromarray((rng.random((gh, gw)) * 255).astype("uint8"))
        total += amp * np.asarray(grid.resize((w, h), Image.BICUBIC), float) / 255.0
        norm += amp
        amp *= 0.5
    total /= norm
    return (total - total.min()) / max(total.max() - total.min(), 1e-6)


def _glow(canvas, cx, cy, radius, colour, strength):
    """Add a soft round light."""
    import numpy as np
    h, w, _ = canvas.shape
    y, x = np.ogrid[:h, :w]
    fall = np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2.0 * radius * radius)) * strength
    canvas += fall[..., None] * (np.array(colour, float) - canvas) * 1.0


def _vignette(canvas, amount):
    import numpy as np
    h, w, _ = canvas.shape
    y, x = np.ogrid[:h, :w]
    d = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
    canvas *= (1.0 - amount * d ** 2)[..., None]


def _finish(canvas, rng, grain=2.0):
    """Fine grain (no banding in smooth gradients), as a picture."""
    import numpy as np
    from PIL import Image
    canvas = canvas + rng.normal(0.0, grain, canvas.shape)
    return Image.fromarray(np.clip(canvas, 0, 255).astype("uint8"), "RGB")


def _studio(w, h, rng):
    import numpy as np
    y, x = np.ogrid[:h, :w]
    d = np.sqrt(((x - w * 0.5) / w) ** 2 + ((y - h * 0.42) / (h * 0.8)) ** 2)
    t = np.clip(d / 0.75, 0, 1)[..., None]
    canvas = np.array([84.0, 86.0, 100.0]) * (1 - t) + np.array([22.0, 23.0, 28.0]) * t
    canvas = canvas * np.ones((h, w, 1))
    _glow(canvas, w * 0.5, h * 1.02, w * 0.45, (70, 72, 84), 0.5)
    _vignette(canvas, 0.35)
    return _finish(canvas, rng, 1.6)


def _dawn(w, h, rng):
    canvas = _grad(h, w, [(0.0, (190, 214, 242)), (0.55, (246, 228, 218)), (1.0, (250, 206, 186))])
    for _ in range(22):
        r = rng.uniform(0.04, 0.13) * w
        tone = (255, 244, 226) if rng.random() < 0.6 else (255, 222, 206)
        _glow(canvas, rng.uniform(0, w), rng.uniform(0.05, 0.95) * h, r, tone, rng.uniform(0.12, 0.35))
    _glow(canvas, w * 0.8, h * 0.18, w * 0.35, (255, 250, 236), 0.35)
    return _finish(canvas, rng)


def _day(w, h, rng):
    import numpy as np
    from PIL import Image, ImageFilter
    canvas = _grad(h, w, [(0.0, (78, 140, 214)), (0.68, (186, 218, 244)), (1.0, (214, 232, 246))])
    cloud = _noise(h, w, rng, cells=3, octaves=4, stretch=(2.2, 0.8))     # finer up and down: wide clouds
    cloud = np.asarray(Image.fromarray((cloud * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(8)), float) / 255
    band = np.exp(-((np.linspace(0, 1, h) - 0.3) / 0.22) ** 2)[:, None]
    t = np.clip((cloud * (0.55 + 0.45 * band) - 0.42) / 0.2, 0, 1)
    cover = t * t * (3 - 2 * t) * (0.25 + 0.75 * band)
    # Soft volume: whiter where the cloud is thickest, a little blue-grey at its thin edges.
    body = np.clip((cloud - 0.45) / 0.3, 0, 1)[..., None]
    white = np.array([212.0, 222.0, 238.0]) * (1 - body) + np.array([252.0, 252.0, 255.0]) * body
    canvas = canvas * (1 - cover[..., None] * 0.9) + white * cover[..., None] * 0.9
    _glow(canvas, w * 0.15, h * 0.08, w * 0.3, (255, 252, 240), 0.3)
    return _finish(canvas, rng)


def _sunset(w, h, rng):
    import numpy as np
    canvas = _grad(h, w, [(0.0, (52, 40, 96)), (0.42, (172, 76, 124)), (0.68, (244, 136, 92)), (1.0, (252, 196, 128))])
    _glow(canvas, w * 0.5, h * 0.74, w * 0.42, (255, 214, 150), 0.65)
    _glow(canvas, w * 0.5, h * 0.74, w * 0.1, (255, 240, 200), 0.8)
    streak = _noise(h, w, rng, cells=4, octaves=5, stretch=(2.6, 0.5))
    band = np.exp(-((np.linspace(0, 1, h) - 0.55) / 0.2) ** 2)[:, None]
    cover = np.clip((streak * band - 0.42) / 0.2, 0, 1)[..., None] * 0.55
    canvas = canvas * (1 - cover) + np.array([120.0, 58.0, 96.0]) * cover
    return _finish(canvas, rng)


def _night(w, h, rng):
    import numpy as np
    canvas = _grad(h, w, [(0.0, (8, 10, 30)), (0.7, (20, 24, 60)), (1.0, (34, 38, 82))])
    y, x = np.mgrid[:h, :w]
    along = (x / w - 0.15) * 0.8 - (y / h - 0.1) * 0.45
    band = np.exp(-(along / 0.12) ** 2) * _noise(h, w, rng, cells=5, octaves=6)
    canvas += band[..., None] * np.array([54.0, 48.0, 92.0]) * 0.6
    count = 1100
    xs, ys = rng.integers(0, w, count), rng.integers(0, int(h * 0.92), count)
    bright = rng.pareto(2.5, count)
    for xi, yi, b in zip(xs, ys, bright):
        level = min(255.0, 90 + 70 * b)
        tint = np.array([1.0, 0.95, 0.85]) if rng.random() < 0.3 else np.array([0.85, 0.92, 1.0])
        canvas[yi, xi] = np.maximum(canvas[yi, xi], level * tint)
        if b > 2.2:
            _glow(canvas[max(0, yi - 40):yi + 40, max(0, xi - 40):xi + 40], min(40, xi), min(40, yi), 6.0,
                  tuple(level * tint), 0.5)
    return _finish(canvas, rng, 1.2)


def _room(w, h, rng):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    floor_at = int(h * 0.8)
    wall = _grad(h, w, [(0.0, (228, 208, 190)), (0.8, (212, 186, 166)), (0.8001, (150, 104, 76)), (1.0, (116, 80, 58))])
    img = Image.fromarray(wall.astype("uint8"), "RGB")
    d = ImageDraw.Draw(img, "RGBA")
    for k in range(8):                                   # floorboards
        yy = floor_at + int((h - floor_at) * (k + 1) / 9)
        d.line([(0, yy), (w, yy)], fill=(90, 60, 42, 60), width=3)
    d.rectangle([0, floor_at - 22, w, floor_at], fill=(240, 228, 214, 255))     # skirting
    wx0, wy0, wx1, wy1 = int(w * 0.07), int(h * 0.13), int(w * 0.44), int(h * 0.47)
    sky = Image.fromarray(_grad(wy1 - wy0, wx1 - wx0, [(0, (150, 196, 236)), (1, (214, 232, 246))]).astype("uint8"), "RGB")
    img.paste(sky, (wx0, wy0))
    d.rectangle([wx0, wy0, wx1, wy1], outline=(246, 242, 236, 255), width=16)
    d.line([((wx0 + wx1) // 2, wy0), ((wx0 + wx1) // 2, wy1)], fill=(246, 242, 236, 255), width=12)
    d.line([(wx0, (wy0 + wy1) // 2), (wx1, (wy0 + wy1) // 2)], fill=(246, 242, 236, 255), width=12)
    d.polygon([(wx0, wy1), (wx1, wy1), (int(w * 0.7), floor_at + int(h * 0.08)), (int(w * 0.18), floor_at + int(h * 0.08))],
              fill=(255, 246, 226, 40))                  # light from the window on the floor
    sy, sx0, sx1 = int(h * 0.36), int(w * 0.58), int(w * 0.95)
    x = sx0 + 8
    palette = [(178, 92, 82), (92, 120, 160), (220, 182, 96), (120, 150, 110), (160, 120, 170), (210, 140, 110)]
    while x < sx1 - 120:
        bw, bh = int(rng.integers(26, 48)), int(rng.integers(90, 150))
        d.rectangle([x, sy - bh, x + bw, sy], fill=palette[int(rng.integers(0, len(palette)))] + (255,))
        x += bw + int(rng.integers(2, 8))
    d.rectangle([sx0, sy, sx1, sy + 18], fill=(170, 124, 92, 255))           # shelf
    px, py = sx1 - 64, sy - 60
    d.rectangle([px - 34, py + 10, px + 34, sy], fill=(196, 128, 96, 255))   # plant pot
    for dx, dy, r in ((-30, -10, 34), (22, -24, 38), (0, -52, 36), (-46, -40, 26), (40, -58, 24)):
        d.ellipse([px + dx - r, py + dy - r, px + dx + r, py + dy + r], fill=(96, 146, 98, 255))
    img = img.filter(ImageFilter.GaussianBlur(5))        # out of focus behind the character
    canvas = np.asarray(img, float).copy()
    _glow(canvas, w * 0.82, h * 0.06, w * 0.28, (255, 226, 170), 0.45)      # a warm lamp
    _vignette(canvas, 0.25)
    return _finish(canvas, rng)


def _blossom(w, h, rng):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    canvas = _grad(h, w, [(0.0, (252, 232, 238)), (1.0, (242, 192, 212))])
    _glow(canvas, w * 0.3, h * 0.1, w * 0.4, (255, 250, 246), 0.5)
    base = Image.fromarray(canvas.astype("uint8"), "RGB").convert("RGBA")
    for blur, count, size in ((6, 14, (40, 70)), (1, 110, (12, 26))):
        layer = Image.new("RGBA", (w, h), (248, 186, 206, 0))    # see-through petal colour: blurring keeps it pink
        d = ImageDraw.Draw(layer)
        for _ in range(count):
            cx, cy = rng.uniform(0, w), rng.uniform(0, h)
            length = rng.uniform(*size)
            width = length * rng.uniform(0.45, 0.6)
            turn = rng.uniform(0, math.pi)
            points = []
            for k in range(24):                          # a petal: an ellipse a little pointed at one end
                a = 2 * math.pi * k / 24
                px, py = length / 2 * math.cos(a), width / 2 * math.sin(a) * (1 - 0.35 * max(0.0, math.cos(a)))
                points.append((cx + px * math.cos(turn) - py * math.sin(turn), cy + px * math.sin(turn) + py * math.cos(turn)))
            tone = (246, 168, 192) if rng.random() < 0.5 else (252, 206, 220)
            d.polygon(points, fill=tone + (int(rng.uniform(150, 230)),))
        base = Image.alpha_composite(base, layer.filter(ImageFilter.GaussianBlur(blur)))
    return _finish(np.asarray(base.convert("RGB"), float), rng, 1.2)


def _paper(w, h, rng):
    import numpy as np
    canvas = np.ones((h, w, 3)) * np.array([244.0, 238.0, 226.0])
    canvas += (_noise(h, w, rng, cells=40, octaves=3) - 0.5)[..., None] * 14
    canvas += (_noise(h, w, rng, cells=12, octaves=2, stretch=(4.0, 0.3)) - 0.5)[..., None] * 8
    _vignette(canvas, 0.18)
    return _finish(canvas, rng, 1.4)


DRAW = {"studio": _studio, "dawn": _dawn, "day": _day, "sunset": _sunset, "night": _night, "room": _room,
        "blossom": _blossom, "paper": _paper}


def draw_builtin(name, size=SIZE):
    """One built-in picture, the same every time it is drawn."""
    import numpy as np
    rng = np.random.default_rng(sorted(BUILTIN).index(name) + 101)
    return DRAW[name](size[0], size[1], rng)


# ---------------------------------------------------------------- the owner's pictures, and choices

class Backgrounds:
    def __init__(self, data_dir):
        self.folder = os.path.join(data_dir, "backgrounds")
        self.builtin_folder = os.path.join(self.folder, "builtin")
        self.thumb_folder = os.path.join(self.folder, "thumbs")
        self.index_path = os.path.join(self.folder, "backgrounds.json")

    def index(self):
        try:
            with open(self.index_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if ID.fullmatch(k) and isinstance(v, dict)
                and os.path.isfile(os.path.join(self.folder, k + ".jpg"))}

    def pictures(self):
        """Every choice, for the settings page: the special ones, the built-in ones, then the owner's."""
        out = [{"id": k, "title": t, "kind": "special"} for k, t in SPECIAL.items()]
        out += [{"id": k, "title": t, "kind": "builtin"} for k, t in BUILTIN.items()]
        out += [{"id": k, "title": e.get("title") or "My picture", "kind": "yours", "added": e.get("added")}
                for k, e in sorted(self.index().items(), key=lambda kv: kv[1].get("added", 0))]
        return out

    def known(self, choice):
        return choice in SPECIAL or choice in BUILTIN or choice in self.index()

    def resolve(self, choice, hour=None):
        """The picture a choice shows now: "auto" by the hour, "none" (or an unknown one) nothing."""
        if choice == "auto":
            hour = time.localtime().tm_hour if hour is None else hour
            return AUTO[part_of_day(hour)]
        if choice in BUILTIN or choice in self.index():
            return choice
        return None

    def path(self, picture):
        """The file of a picture (drawing a built-in one the first time)."""
        if picture in BUILTIN:
            path = os.path.join(self.builtin_folder, picture + ".jpg")
            if not os.path.isfile(path):
                os.makedirs(self.builtin_folder, exist_ok=True)
                out = io.BytesIO()
                draw_builtin(picture).save(out, "JPEG", quality=90, optimize=True)
                write_atomic(path, out.getvalue())
            return path
        if picture in self.index():
            return os.path.join(self.folder, picture + ".jpg")
        raise KeyError(picture)

    def thumbnail(self, picture):
        """A small JPEG of a picture, for the settings page."""
        from PIL import Image
        path = os.path.join(self.thumb_folder, picture + ".jpg")
        source = self.path(picture)
        if not os.path.isfile(path) or os.path.getmtime(path) < os.path.getmtime(source):
            with Image.open(source) as im:
                im = im.convert("RGB")
                scale = max(THUMB[0] / im.width, THUMB[1] / im.height)
                im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
                left, top = (im.width - THUMB[0]) // 2, (im.height - THUMB[1]) // 2
                im = im.crop((left, top, left + THUMB[0], top + THUMB[1]))
                out = io.BytesIO()
                im.save(out, "JPEG", quality=82)
            os.makedirs(self.thumb_folder, exist_ok=True)
            write_atomic(path, out.getvalue())
        with open(path, "rb") as f:
            return f.read()

    def add(self, data, title=""):
        """Keep a picture of the owner's. Returns its id. It is turned upright, made no larger than the screen
        needs, and kept without the details a camera stores with it (where it was taken, among them)."""
        from PIL import Image, ImageOps
        if len(data) > MAX_BYTES:
            raise BackgroundError("the picture is too large (%d MB; at most %d MB)" % (len(data) >> 20, MAX_BYTES >> 20))
        index = self.index()
        if len(index) >= MAX_PICTURES:
            raise BackgroundError("there can be at most %d pictures of your own; remove one first" % MAX_PICTURES)
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.width * im.height > MAX_PIXELS:
                    raise BackgroundError("the picture has too many pixels (at most %d million)" % (MAX_PIXELS // 1_000_000))
                im = ImageOps.exif_transpose(im)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    flat = Image.new("RGB", im.size, (16, 16, 20))
                    flat.paste(im, mask=im.getchannel("A"))
                    im = flat
                else:
                    im = im.convert("RGB")
                if min(im.size) < SMALLEST:
                    raise BackgroundError("the picture is too small (at least %d pixels on each side)" % SMALLEST)
                im.thumbnail((LONGEST, LONGEST), Image.LANCZOS)
                out = io.BytesIO()
                im.save(out, "JPEG", quality=88, optimize=True)        # no EXIF: nothing of the camera's is kept
        except BackgroundError:
            raise
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise BackgroundError("this is not a picture the device can read (%s); use a JPEG or PNG" % error)
        name = re.sub(r"[^a-z0-9]+", "_", str(title or "my picture").lower()).strip("_")[:30] or "my_picture"
        picture = "my_%s_%s" % (name, secrets.token_hex(2))
        os.makedirs(self.folder, exist_ok=True)
        write_atomic(os.path.join(self.folder, picture + ".jpg"), out.getvalue())
        index[picture] = {"title": " ".join(str(title or "").split())[:60] or "My picture", "added": int(time.time())}
        write_atomic(self.index_path, json.dumps(index, indent=1))
        return picture

    def remove(self, picture):
        index = self.index()
        if index.pop(picture, None) is None:
            return False
        for path in (os.path.join(self.folder, picture + ".jpg"), os.path.join(self.thumb_folder, picture + ".jpg")):
            if os.path.isfile(path):
                os.remove(path)
        write_atomic(self.index_path, json.dumps(index, indent=1))
        return True

    def choice_for(self, settings, character):
        """(choice, dim, parallax) for a character: its own choice, or the device's."""
        default = settings.get("background") or {}
        own = (character or {}).get("background") or {}
        choice = own.get("choice") if own.get("choice") not in (None, "default") else default.get("choice", "auto")
        if not self.known(choice):
            choice = "auto"
        dim = own.get("dim") if isinstance(own.get("dim"), (int, float)) else default.get("dim", 0.0)
        return choice, float(dim or 0.0), bool(default.get("parallax", True))

    def stage_spec(self, settings, character, hour=None):
        """What the stage draws behind a character: {"path", "dim", "parallax"}, or None for nothing."""
        choice, dim, parallax = self.choice_for(settings, character)
        picture = self.resolve(choice, hour)
        if picture is None:
            return None
        try:
            path = self.path(picture)
        except (KeyError, OSError):
            return None
        return {"path": path, "dim": round(min(max(dim, 0.0), 0.8), 2), "parallax": 1.0 if parallax else 0.0}
