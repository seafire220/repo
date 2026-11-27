#!/usr/bin/env python3
"""Improve a rigged puppet's head movement and mouth.

Head movement
-------------

The image2live2d rigger turns the head by rotating the whole head group as
one flat card about a hinge far below the neck. An earlier version of this
tool replaced that with a warp that slid every mesh point separately, which
stretched and squashed the eyes and mouth while the head itself stayed put.

This version follows how hand-made Live2D and Inochi2D puppets are built:

  * Tilt: the whole head (face, features, hair) leans as one rigid piece
    about a point in the neck. The neck bends to follow, and long hair fades
    out of the movement below the neck so it hangs instead of swinging.
  * Turn and nod: the head is treated as a rounded surface. The middle of
    the face slides furthest, the cheeks and chin less, and the outline of
    the head not at all. Eyes, eyebrows, nose and mouth each move as a
    rigid piece by the amount at their own centre, so they never change
    shape. The face and hair bend smoothly around them.
  * The nose stands out from the face, so it goes further than the eyes,
    and at the end of a turn it sits as far to that side of the eyes'
    middle whichever way the head turns, wherever it was drawn. A nose
    drawn on the face's skin (most are) is found on the face's picture and
    the skin round it follows it. (Moving with the eyes exactly, a nose
    drawn a little to one side led a turn that way and lagged behind a turn
    the other way, which then looked like a slide, not a turn.)

Mouth
-----
The rigger opens the mouth by stretching the drawn mouth line, which is
often only a few pixels high, so the mouth hardly moves. partsplit.py draws
an open mouth sized to the face, hidden at rest. Here the mouth line fades
out as the mouth opens and the open mouth grows down from the line.

Body turn and lean
------------------
The rigger's body turn slid the whole head 40 pixels sideways (and its
lean 30 up or down), leaving empty space where the head had been. Now the
upper body turns as a cylinder about the spine: the middle of the chest
moves most, the outline is held and the turn dies out towards the hips.
The neck and head ride on the spine, so they move a few pixels, together;
each arm moves with its shoulder. A lean raises or lowers the shoulders,
neck and head together.

Arms
----
The rigger cuts each arm at the elbow into an upper arm and a forearm that
overlap there, and turns them by storing moved copies of their points at
the ends of the A (shoulder) and B (elbow) controls. Between the ends the
points slide in straight lines, so a swinging arm shortens; and the two
controls add up their movements instead of one following the other, so a
raised arm that also bends comes apart at the elbow. Here the whole arm
hangs from a node at the shoulder that truly rotates (A), and the forearm
turns about the elbow inside it (B, stored every 10 degrees, so the
straight lines between are within a pixel). The controls put named poses
at fixed values, solved for each character's arm from its lengths and how
it is drawn: a waving arm at A 5 and B 8.5, a hand on the hip at A 6 and
B -9 (see ARM_IN and the values below it).

Blink, breathing and hair physics are left as the rigger made them.

Usage: refine.py IN.inp OUT.inp
"""
import json
import math
import struct
import sys

MAGIC = b"TRNSRTS\0"
ANGLE_PARAMS = ("ParamAngleX", "ParamAngleY", "ParamAngleZ")

# Turn and nod: how far the head turns at the end of the control's range.
TURN_ANGLE = math.radians(18)
NOD_ANGLE = math.radians(12)
# How far each kind of part stands out from the rounded head surface, in
# head half-widths: the nose stands out and so moves furthest. Parts not
# listed lie on the surface. Hair at the back lies behind the middle of the
# head, at this share of the front surface's depth, so it moves the other way.
DEPTH = {"nose": 0.18, "fringe": 0.06, "ears": -0.12}
BACK_DEPTH = 0.6
# A nose drawn on the face's skin: how much darker than the skin round it its mark is (0-255), and how
# far round the mark the skin goes with it, in face half-widths (the skin there stretches a little).
NOSE_DARKER = 25
NOSE_REACH = 0.35
# A mark further than this to the side of the eyes' middle (face half-widths) is not taken for a nose.
# A nose drawn to one side is made up for by at most NOSE_EVEN of its own lead, so it still goes the turn's
# way at both ends (a face drawn half-turned keeps some of its own turn; only its own drawing can undo it).
NOSE_ASIDE = 0.3
NOSE_EVEN = 0.9
# Near the head's outline, points are held in place, so the outline only
# moves a little and nothing behind the head is uncovered. This is the depth
# below the surface (0 at the outline, 1 at the middle) where holding stops.
OUTLINE_HOLD = 0.35
# Where the head turns and nods about: this far behind its middle (in head half-widths), so the whole
# head shifts a little with the turn, as Live2D artists move the outline "slightly towards the turn".
TURN_AXIS = 0.35
NOD_AXIS = 0.2
# Each part follows a coarse grid of points, the way a Live2D artist puts each
# part in its own warp deformer: small parts and features keep their shape.
GRIDS = {"eye": (3, 3), "mouth": (3, 2), "nose": (2, 2), "blush": (2, 2)}
GRID_DEFAULT = (3, 3)
FEATURE_KINDS = ("eye", "mouth", "nose", "blush", "ear")
# Head lean at the end of the range, in radians (about 9 degrees).
MAX_TILT = 0.16
# The rounded surface: half-width as a share of face width (limits), and how
# far below the chin it ends, as a multiple of the eye-to-chin distance.
SURFACE_MIN = 0.62
SURFACE_MAX = 0.95
SURFACE_BELOW = 1.25
# Where the head leans from: this share of the way down the visible neck,
# but no further below the chin than this share of the face width.
PIVOT_DOWN_NECK = 0.5
PIVOT_LIMIT = 0.3
# Below the pivot, hair leaves the lean over this distance (in face widths).
HAIR_FADE = 0.9

# Parts that must keep their shape: each group moves as one rigid piece.
RIGID_GROUPS = {
    "eye_l": ("eye_l", "eye_white_l", "pupil_l", "eye_closed_l"),
    "eye_r": ("eye_r", "eye_white_r", "pupil_r", "eye_closed_r"),
    "mouth": ("mouth", "mouth_cavity"),
    "nose": ("nose",),
    "eyebrow_l": ("eyebrow_l",),
    "eyebrow_r": ("eyebrow_r",),
}


def read_puppet(data):
    if data[:8] != MAGIC:
        raise ValueError("not an Inochi2D puppet file")
    length = struct.unpack(">I", data[8:12])[0]
    return json.loads(data[12:12 + length]), data[12 + length:]


def write_puppet(puppet, rest):
    payload = json.dumps(puppet, separators=(",", ":")).encode()
    return MAGIC + struct.pack(">I", len(payload)) + payload + rest


def smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def role_of(name):
    """'30_eye_white_l' -> 'eye_white_l'."""
    head, _, tail = name.partition("_")
    return tail if head.isdigit() else name


def walk_parts(node, origin=(0.0, 0.0), under=None, out=None, parents=()):
    """Every mesh part with its position on the canvas and the names of the nodes above it."""
    out = [] if out is None else out
    transform = node.get("transform", {})
    trans = transform.get("trans", [0.0, 0.0, 0.0])
    rot = transform.get("rot", [0.0, 0.0, 0.0])
    scale = transform.get("scale", [1.0, 1.0])
    if any(abs(r) > 1e-6 for r in rot) or any(abs(s - 1.0) > 1e-6 for s in scale):
        raise ValueError("node '%s' is rotated or scaled at rest; only plain offsets are supported" % node.get("name"))
    here = (origin[0] + trans[0], origin[1] + trans[1])
    if node.get("type") == "Part" and node.get("mesh", {}).get("verts"):
        out.append({"node": node, "origin": here, "parents": parents, "role": role_of(node.get("name", ""))})
    for child in node.get("children") or []:
        walk_parts(child, here, under, out, parents + (node.get("name"),))
    return out


def points(part):
    verts = part["node"]["mesh"]["verts"]
    ox, oy = part["origin"]
    return [(verts[i] + ox, verts[i + 1] + oy) for i in range(0, len(verts), 2)]


def box(point_list):
    xs = [p[0] for p in point_list]
    ys = [p[1] for p in point_list]
    return min(xs), min(ys), max(xs), max(ys)


def head_geometry(parts):
    """Measure the head: face, eye line, rounded surface and the point it leans from."""
    head_parts = [p for p in parts if "head" in p["parents"]]
    face = next((p for p in head_parts if p["role"] == "face_base"), None)
    if face is None:
        raise ValueError("the puppet has no face part under its head")
    left, top, right, chin = box(points(face))
    width = right - left
    if width <= 0 or chin <= top:
        raise ValueError("the face part has no size")
    centre_x = (left + right) / 2.0

    groups = {}
    for part in head_parts:
        for group, roles in RIGID_GROUPS.items():
            if part["role"] in roles:
                groups.setdefault(group, []).append(part)
    anchors = {}
    for group, members in groups.items():
        x0, y0, x1, y1 = box([pt for m in members for pt in points(m)])
        anchors[group] = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    eye_ys = [anchors[g][1] for g in ("eye_l", "eye_r") if g in anchors]
    eye_line = sum(eye_ys) / len(eye_ys) if eye_ys else top + 0.45 * (chin - top)
    eye_line = min(max(eye_line, top + 0.2 * (chin - top)), chin - 0.2 * (chin - top))

    hx0, hy0, hx1, _ = box([pt for p in head_parts for pt in points(p)])
    half_width = min(max((hx1 - hx0) / 2.0, SURFACE_MIN * width), SURFACE_MAX * width)
    up = min(max(eye_line - hy0, 0.5 * width), 1.1 * width)
    down = SURFACE_BELOW * (chin - eye_line)

    neck = next((p for p in parts if p["role"] == "neck"), None)
    pivot = (centre_x, chin)
    neck_bottom = None
    if neck is not None:
        nx0, _, nx1, ny1 = box(points(neck))
        if ny1 > chin:
            neck_bottom = ny1
            pivot = ((nx0 + nx1) / 2.0, chin + min(PIVOT_DOWN_NECK * (ny1 - chin), PIVOT_LIMIT * width))
    return {
        "head_parts": head_parts, "neck": neck, "groups": groups, "anchors": anchors,
        "face_width": width, "centre": (centre_x, eye_line), "half_width": half_width,
        "up": up, "down": down, "chin": chin, "pivot": pivot, "neck_bottom": neck_bottom,
    }


def surface_depth(x, y, g):
    """How far the head's rounded surface stands out at (x, y): 1 at the middle, 0 at the outline."""
    u = (x - g["centre"][0]) / g["half_width"]
    dy = y - g["centre"][1]
    v = dy / (g["up"] if dy < 0 else g["down"])
    return math.sqrt(max(0.0, 1.0 - u * u - v * v))


def turned(x, y, g, turn, nod, depth=(1.0, 0.0)):
    """Turn and nod: where the point at (x, y) goes when the head turns (turn) or nods (nod), each -1..1.

    The head is a rounded solid. Each point is turned with it about the
    head's middle, by its depth, and drawn straight on. `depth` is (side,
    extra): side 1 is the front surface, a negative side behind the middle,
    and extra is for parts that stand out from the surface. So the middle of the face
    moves furthest, the side turning away gets narrower and the near side
    wider, parts in front move more than the face and parts behind move the
    other way. Near the outline points are held, so the outline moves only a
    little. y grows downwards; a positive nod looks up.
    """
    rx = g["half_width"]
    dy = y - g["centre"][1]
    ry = g["up"] if dy < 0 else g["down"]
    u = (x - g["centre"][0]) / rx
    v = dy / ry
    s = surface_depth(x, y, g)
    hold = smoothstep(s / OUTLINE_HOLD)
    z = depth[0] * s + depth[1]
    a, b = TURN_ANGLE * turn, NOD_ANGLE * nod
    u2 = u * math.cos(a) + z * math.sin(a)
    z2 = z * math.cos(a) - u * math.sin(a)
    v2 = v * math.cos(b) - z2 * math.sin(b)
    # The head turns about the neck, which is behind its middle: the whole head, outline and all, also
    # shifts a little towards the turn (and the nod), so the far side stays round instead of being
    # squeezed against an outline that does not move. Hair hanging below the chin is left out.
    hang = 1.0 - smoothstep((v - 0.9) / 0.4) if dy > 0 else 1.0
    shift_x = TURN_AXIS * math.sin(a) * rx * hang
    shift_y = -NOD_AXIS * math.sin(b) * ry * hang
    return ((u2 - u) * rx * hold + shift_x, (v2 - v) * ry * hold + shift_y)


def kind_of(role):
    for key in ("eye", "mouth", "nose", "blush", "face_base", "hair", "ear"):
        if role.startswith(key) or key in role:
            return key
    return role


def depth_at(role, x, y, g):
    """Where a part lies at (x, y), by what it is, as (side, extra) for turned()."""
    kind = kind_of(role)
    if kind == "nose":
        return (1.0, DEPTH["nose"])
    if kind == "ear":
        return (1.0, DEPTH["ears"])
    if role.startswith("hair_back"):
        return (-BACK_DEPTH, 0.0)
    if kind == "hair":
        # The fringe lies over the forehead, in front of it; other hair is on the surface.
        inside = abs(x - g["centre"][0]) < 0.5 * g["face_width"] and y < g["centre"][1]
        return (1.0, DEPTH["fringe"] if inside else 0.0)
    return (1.0, 0.0)


def grid_offsets(pts, box_, shape, field):
    """Offsets for points that follow a coarse grid over box_: the field is worked out at the grid's
    points and spread between them, so a part changes shape smoothly, as in a Live2D warp deformer."""
    x0, y0, x1, y1 = box_
    nx, ny = shape
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    grid = [[field(x0 + w * i / (nx - 1), y0 + h * j / (ny - 1)) for i in range(nx)] for j in range(ny)]
    out = []
    for x, y in pts:
        fx = min(max((x - x0) / w, 0.0), 1.0) * (nx - 1)
        fy = min(max((y - y0) / h, 0.0), 1.0) * (ny - 1)
        i, j = min(int(fx), nx - 2), min(int(fy), ny - 2)
        tx, ty = fx - i, fy - j
        (a, b), (c, d) = grid[j][i], grid[j][i + 1]
        (e, f), (k, m) = grid[j + 1][i], grid[j + 1][i + 1]
        top = (a + (c - a) * tx, b + (d - b) * tx)
        bottom = (e + (k - e) * tx, f + (m - f) * tx)
        out.append((top[0] + (bottom[0] - top[0]) * ty, top[1] + (bottom[1] - top[1]) * ty))
    return out


def lean(x, y, g, tilt, fade):
    """Tilt: rotate about the pivot, fully above it and fading out over `fade` below it."""
    px, py = g["pivot"]
    weight = 1.0 if fade <= 0 else 1.0 - smoothstep((y - py) / fade)
    if y <= py:
        weight = 1.0
    angle = MAX_TILT * tilt * weight
    rx, ry = x - px, y - py
    return (rx * math.cos(angle) - ry * math.sin(angle) - rx, rx * math.sin(angle) + ry * math.cos(angle) - ry)


def find_nose(rest, g):
    """The nose drawn on the face's own picture: the darkest small mark between the eyes and the mouth,
    near the eyes' middle (its line or its shadow). Returns {"x", "y", "size"} in the puppet's
    coordinates (size: half its longer side), or None when there is no such mark."""
    import io
    import numpy as np
    from PIL import Image
    face = next((p for p in g["head_parts"] if p["role"] == "face_base"), None)
    eyes = [box([pt for m in g["groups"][k] for pt in points(m)]) for k in ("eye_l", "eye_r") if k in g["groups"]]
    if face is None or not eyes:
        return None
    textures, _ = read_texture_section(rest)
    index = (face["node"].get("textures") or [None])[0]
    if index is None or index >= len(textures) or textures[index][0] != 0:
        return None                                     # only PNG pictures are read
    with Image.open(io.BytesIO(textures[index][1])) as im:
        rgba = np.asarray(im.convert("RGBA")).astype(float)
    pts = np.array(points(face), float)
    uvs = np.array(face["node"]["mesh"]["uvs"], float).reshape(-1, 2) * [rgba.shape[1], rgba.shape[0]]
    fit = [np.polyfit(pts[:, axis], uvs[:, axis], 1) for axis in (0, 1)]    # picture pixels = a * point + b
    if min(abs(a) for a, _ in fit) < 1e-9:
        return None
    if max(np.abs(np.polyval(fit[axis], pts[:, axis]) - uvs[:, axis]).max() for axis in (0, 1)) > 2.0:
        return None                                     # not drawn straight onto its mesh
    hw = g["face_width"] / 2.0
    eye_mid = sum((x0 + x1) / 2.0 for x0, _, x1, _ in eyes) / len(eyes)
    eye_bottom = max(y1 for _, _, _, y1 in eyes)
    mouth = g["groups"].get("mouth")
    mouth_top = (box([pt for m in mouth for pt in points(m)])[1] if mouth
                 else g["chin"] - 0.35 * (g["chin"] - g["centre"][1]))
    gap = mouth_top - eye_bottom
    if gap < 0.15 * hw:
        return None
    ys, xs = np.mgrid[0:rgba.shape[0], 0:rgba.shape[1]]
    px = (xs + 0.5 - fit[0][1]) / fit[0][0]
    py = (ys + 0.5 - fit[1][1]) / fit[1][0]
    x0, x1 = eye_mid - 0.4 * hw, eye_mid + 0.4 * hw
    y0, y1 = eye_bottom + 0.05 * gap, mouth_top - 0.1 * gap
    area = (px >= x0) & (px <= x1) & (py >= y0) & (py <= y1) & (rgba[..., 3] > 200)
    if area.sum() < 20:
        return None
    lum = rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114
    skin = float(np.median(lum[area]))
    pixel = 1.0 / abs(fit[0][0])                        # one picture pixel, in puppet units
    want = (eye_mid, eye_bottom + 0.45 * gap)
    best, best_score = None, 0.0
    for mark in _marks(area & (lum < skin - NOSE_DARKER)):
        if mark.sum() < 6:
            continue                                    # a speck
        mx, my = px[mark], py[mark]
        if mx.min() <= x0 + pixel or mx.max() >= x1 - pixel:
            continue                                    # comes in from the side: hair or shading
        size = max(mx.max() - mx.min(), my.max() - my.min(), pixel)
        if not 0.03 * hw <= size <= 0.6 * hw:
            continue
        weight = skin - lum[mark]
        cx, cy = float((mx * weight).sum() / weight.sum()), float((my * weight).sum() / weight.sum())
        if abs(cx - eye_mid) > NOSE_ASIDE * hw:
            continue                                    # too far to the side for a nose
        distance = math.hypot(cx - want[0], 0.5 * (cy - want[1])) / (0.25 * hw)
        score = float(weight.sum()) / (1.0 + distance * distance)
        if score > best_score:
            best, best_score = {"x": cx, "y": cy, "size": float(size) / 2.0}, score
    return best


def _marks(dark):
    """Each separate patch of a true/false picture (touching sideways or corner to corner), as masks."""
    import numpy as np
    seen = np.zeros(dark.shape, bool)
    height, width = dark.shape
    for start in zip(*np.nonzero(dark)):
        if seen[start]:
            continue
        seen[start] = True
        patch, todo = [], [start]
        while todo:
            y, x = todo.pop()
            patch.append((y, x))
            for ny in (y - 1, y, y + 1):
                for nx in (x - 1, x, x + 1):
                    if 0 <= ny < height and 0 <= nx < width and dark[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        todo.append((ny, nx))
        mask = np.zeros(dark.shape, bool)
        mask[tuple(np.array(patch).T)] = True
        yield mask


def nose_of(g, rest=None):
    """The nose to move on its own: its own part if it has one, else the mark on the face's picture."""
    if "nose" in g["groups"]:
        x0, y0, x1, y1 = box([pt for m in g["groups"]["nose"] for pt in points(m)])
        nose = {"x": (x0 + x1) / 2.0, "y": (y0 + y1) / 2.0, "size": max(x1 - x0, y1 - y0) / 2.0, "part": True}
    elif rest is not None:
        nose = find_nose(rest, g)
        if nose is None:
            return None
        nose["part"] = False
    else:
        return None
    eyes = [g["anchors"][k] for k in ("eye_l", "eye_r") if k in g["anchors"]]
    if not eyes:
        return None
    nose["drawn"] = nose["x"] - sum(x for x, _ in eyes) / len(eyes)     # to the side of the eyes' middle
    return nose


def nose_shift(g, name, position):
    """How much further than the face beneath it the nose goes at this end of a control's range, (dx, dy).

    Turning, it ends as far to the turn's side of the eyes' middle as standing out from the face takes it
    (DEPTH), wherever it was drawn (within NOSE_EVEN), so a turn either way looks alike. Nodding, a drawn
    nose stands out the same way (a nose part does already)."""
    nose = g.get("nose")
    if nose is None or position == 0.0:
        return (0.0, 0.0)
    if name == "ParamAngleX":
        eyes = [g["anchors"][k] for k in ("eye_l", "eye_r") if k in g["anchors"]]
        depth = (1.0, DEPTH["nose"]) if nose["part"] else (1.0, 0.0)
        moved = (turned(nose["x"], nose["y"], g, position, 0.0, depth)[0]
                 - sum(turned(x, y, g, position, 0.0)[0] for x, y in eyes) / len(eyes))
        lead = DEPTH["nose"] * math.sin(TURN_ANGLE) * g["half_width"]
        evened = min(max(nose["drawn"], -NOSE_EVEN * lead), NOSE_EVEN * lead)
        return (position * lead - evened - moved, 0.0)
    if name == "ParamAngleY" and not nose["part"]:
        ry = g["down"] if nose["y"] > g["centre"][1] else g["up"]
        return (0.0, -DEPTH["nose"] * math.sin(NOD_ANGLE * position) * ry)
    return (0.0, 0.0)


def nose_weight(x, y, g):
    """How much of the nose's own movement the face's skin at (x, y) takes: all of it on the mark, none
    beyond NOSE_REACH round it."""
    nose = g["nose"]
    reach = NOSE_REACH * g["face_width"] / 2.0
    inner = max(nose["size"], 0.04 * g["face_width"])
    return smoothstep((inner + reach - math.hypot(x - nose["x"], y - nose["y"])) / reach)


def offsets_for(part, g, name, position):
    """Per-point movement of one part for one control at one end of its range."""
    pts = points(part)
    group = next((k for k, members in g["groups"].items() if part in members), None)
    if name == "ParamAngleZ":
        if part is g["neck"]:
            fade = max(g["neck_bottom"] - g["pivot"][1], 1e-6)
        else:
            fade = HAIR_FADE * g["face_width"]
        return [lean(x, y, g, position, fade) for x, y in pts]
    turn = position if name == "ParamAngleX" else 0.0
    nod = position if name == "ParamAngleY" else 0.0
    if turn == 0.0 and nod == 0.0:
        return [(0.0, 0.0)] * len(pts)
    role = part["role"]
    kind = kind_of(role)
    nose = g.get("nose")
    if group is None and kind not in FEATURE_KINDS:
        # Face, hair and the like follow the rounded surface point by point, so the hold at the
        # outline is exact.
        out = [turned(x, y, g, turn, nod, depth_at(role, x, y, g)) for x, y in pts]
        if role == "face_base" and nose is not None and not nose["part"]:
            sx, sy = nose_shift(g, name, position)
            out = [(dx + sx * w, dy + sy * w) for (dx, dy), w in zip(out, (nose_weight(x, y, g) for x, y in pts))]
        return out
    # A feature follows a coarse grid, so it keeps its shape; the pieces of a group (an eye with its
    # closed eye, the mouth with its open mouth) share the group's grid, so they stay together.
    members = g["groups"][group] if group is not None else [part]
    area = box([pt for m in members for pt in points(m)])
    shape = GRIDS.get(kind, GRID_DEFAULT)
    out = grid_offsets(pts, area, shape, lambda x, y: turned(x, y, g, turn, nod, depth_at(role, x, y, g)))
    if group == "nose" and nose is not None and nose["part"]:
        sx, sy = nose_shift(g, name, position)
        out = [(dx + sx, dy + sy) for dx, dy in out]
    return out


def refine(puppet, rest=None):
    """Rewrite the head-angle controls in place. Returns a short report. With the file's pictures (rest),
    a nose drawn on the face's skin is found and moved on its own."""
    parts = walk_parts(puppet["nodes"])
    g = head_geometry(parts)
    g["nose"] = nose_of(g, rest)
    params = {p["name"]: p for p in puppet.get("param", [])}
    missing = [name for name in ANGLE_PARAMS if name not in params]
    if missing:
        raise ValueError("the puppet lacks head-angle controls: %s" % ", ".join(missing))
    head_node = next(n for n in _nodes(puppet["nodes"]) if n.get("name") == "head")
    moved = list(g["head_parts"])
    ids = {p["node"]["uuid"] for p in moved}
    if g["neck"] is not None and g["neck_bottom"] is not None:
        ids.add(g["neck"]["node"]["uuid"])
    report = {"head_parts": len(moved), "rigid_groups": sorted(g["groups"]), "neck_bends": g["neck_bottom"] is not None,
              "removed": 0, "added": 0}
    if g["nose"] is not None:
        report["nose"] = {"own_part": g["nose"]["part"],
                          "drawn_aside": round(g["nose"]["drawn"] / (g["face_width"] / 2.0), 3)}
    for name in ANGLE_PARAMS:
        param = params[name]
        if param["axis_points"] != [[0.0, 0.5, 1.0], [0.0]]:
            raise ValueError("%s does not have the expected three positions" % name)
        kept = []
        for binding in param["bindings"]:
            rigid = binding["node"] == head_node["uuid"] and binding["param_name"].startswith("transform.")
            ours = binding["node"] in ids and binding["param_name"] == "deform"
            if rigid or ours:
                report["removed"] += 1
            else:
                kept.append(binding)
        targets = list(moved)
        if name == "ParamAngleZ" and g["neck"] is not None and g["neck_bottom"] is not None:
            targets.append(g["neck"])
        for part in targets:
            columns = []
            for position in (-1.0, 0.0, 1.0):
                columns.append([[[round(dx, 3), round(dy, 3)] for dx, dy in offsets_for(part, g, name, position)]])
            kept.append({
                "node": part["node"]["uuid"],
                "param_name": "deform",
                "values": columns,
                "isSet": [[True], [True], [True]],
                "interpolate_mode": "Linear",
            })
            report["added"] += 1
        param["bindings"] = kept
    report.update(refine_mouth(puppet, parts))
    report.update(refine_body(puppet, parts, g))
    report.update(refine_arms(puppet))
    return report


BODY_PARAMS = ("ParamBodyAngleX", "ParamBodyAngleY")
# Body turn at the end of its range, and how far behind the chest's front the spine is (share of the
# body's half-width): the neck and head ride on the spine, so they move much less than the chest.
BODY_TURN_ANGLE = math.radians(8)
SPINE_DEPTH = 0.3
# Body lean: how far the shoulders, neck and head rise (lean back) or drop (lean forward) at the end
# of the range, as a share of the body's half-width.
BODY_LEAN = 0.05
BODY_BANDS = 40
# The body's outline is held with a wide, gentle fade (the share of the half-width's depth over which
# movement fades out towards the outline): smooth enough for a Live2D-format warp grid to follow.
BODY_OUTLINE_HOLD = 0.9


def body_geometry(parts):
    """The upper body: its middle, half-width, shoulder line and where the turn has died out (hips)."""
    torso = next((p for p in parts if p["role"] == "torso"), None)
    if torso is None:
        return None
    pts = points(torso)
    x0, y0, x1, y1 = box(pts)
    arms = [p for p in parts if p["role"].startswith("arm")]
    shoulders = min([y0] + [box(points(a))[1] for a in arms])
    # The body's own width at each height, so the turn holds its real outline, not its widest point's.
    bands = []
    step = max((y1 - y0) / BODY_BANDS, 1e-6)
    for i in range(BODY_BANDS):
        lo, hi = y0 + i * step, y0 + (i + 1) * step
        xs = [x for x, y in pts if lo <= y <= hi]
        if xs:
            bands.append(((lo + hi) / 2.0, min(xs), max(xs)))
    return {"centre": (x0 + x1) / 2.0, "half_width": max((x1 - x0) / 2.0, 1e-6), "shoulders": shoulders,
            "hips": shoulders + 0.65 * (y1 - shoulders), "bands": bands}


def body_width_at(b, y):
    """The body's middle and half-width at height y, from its outline."""
    bands = b["bands"]
    if not bands:
        return b["centre"], b["half_width"]
    if y <= bands[0][0]:
        _, l, r = bands[0]
    elif y >= bands[-1][0]:
        _, l, r = bands[-1]
    else:
        for (ya, la, ra), (yb, lb, rb) in zip(bands, bands[1:]):
            if ya <= y <= yb:
                t = (y - ya) / max(yb - ya, 1e-6)
                l, r = la + (lb - la) * t, ra + (rb - ra) * t
                break
    return (l + r) / 2.0, max((r - l) / 2.0, 1e-6)


def body_moved(x, y, b, turn, lean, spine_only=False):
    """Where a point of the upper body goes when it turns (turn) or leans (lean), each -1..1.

    The chest turns as an upright cylinder about the spine: its middle moves most, the side turning away
    narrows, the outline is held. The turn fades out towards the hips. Near the shoulder line the body
    is at the spine's depth, so the collar moves with the neck. spine_only gives the spine's own
    movement at that height, which the neck and head follow.
    """
    up = 1.0 - smoothstep((y - b["shoulders"]) / max(b["hips"] - b["shoulders"], 1e-6))
    # Above the shoulders (neck, head, hair on the head) everything moves as the shoulder line does there,
    # which near the middle is the spine's movement. Kept continuous, so it is one smooth field.
    y = max(y, b["shoulders"])
    if spine_only:
        u, s, hold, front = 0.0, 0.0, 1.0, 0.0
        rb = body_width_at(b, y)[1]
    else:
        middle, rb = body_width_at(b, y)
        u = (x - middle) / rb
        s = math.sqrt(max(0.0, 1.0 - u * u))
        hold = smoothstep(s / BODY_OUTLINE_HOLD)
        front = smoothstep((y - b["shoulders"]) / max(0.3 * (b["hips"] - b["shoulders"]), 1e-6))
    z = SPINE_DEPTH + (1.0 - SPINE_DEPTH) * s * front
    a = BODY_TURN_ANGLE * turn * up
    dx = (u * math.cos(a) + z * math.sin(a) - u) * rb * hold
    dy = -BODY_LEAN * lean * up * b["half_width"]
    return dx, dy


def refine_body(puppet, parts, g):
    """Body turn and lean: replace the rigger's, which slid the whole head sideways or up and down.

    The upper body turns as a cylinder about the spine; above the shoulders everything rides on the
    spine, so the neck and head move a little, together. Every part follows the same field.
    """
    b = body_geometry(parts)
    params = {p["name"]: p for p in puppet.get("param", [])}
    if b is None:
        return {"body": "left as rigged"}
    done = []
    for name in BODY_PARAMS:
        param = params.get(name)
        if param is None or len(param["axis_points"][0]) != 3:
            continue
        kept = [x for x in param["bindings"] if x["param_name"] != "deform"]
        for part in parts:
            uid = part["node"]["uuid"]
            pts = points(part)
            columns = []
            for position in (-1.0, 0.0, 1.0):
                turn = position if name == "ParamBodyAngleX" else 0.0
                lean = position if name == "ParamBodyAngleY" else 0.0
                # One movement for the whole body, a smooth function of position, so parts that overlap
                # (hair on the chest, arms by the body, the head on the neck) move alike and nothing
                # behind them is uncovered. Above the shoulders it is the spine's movement.
                offsets = [body_moved(x, y, b, turn, lean) for x, y in pts]
                columns.append([[[round(dx, 3), round(dy, 3)] for dx, dy in offsets]])
            if any(abs(v) > 1e-9 for column in columns for pair in column[0] for v in pair):
                kept.append({"node": uid, "param_name": "deform", "values": columns,
                             "isSet": [[True], [True], [True]], "interpolate_mode": "Linear"})
        param["bindings"] = kept
        done.append(name)
    return {"body": done}


# Arms. Angles are measured from straight down, outward positive. The controls run from -10 to 10;
# each puts named poses at fixed values, solved for each character's own arm (its lengths and how it
# is drawn), so a gesture asking for "hand on hip" gets the hand on that character's hip:
#   A (shoulder): -10 slightly in, 0 as drawn, 5 the waving arm, 6 the arm of a hand on the hip,
#                 10 out and up;
#   B (elbow):    -10 past the hip, -9 hand on hip, 0 as drawn, 8.5 forearm up to wave, 10 past it.
# stage/motions/library.json uses the same values (0.5 and 0.85 for a wave, 0.6 and -0.9 for hips).
ARM_IN, ARM_UP = -15.0, 80.0          # upper arm's angle at A = -10 and 10
WAVE_UPPER, WAVE_FORE = 35.0, 160.0   # waving: upper arm out a little, forearm up and a little out
HIP_UP = 0.22                         # hand on hip: the hand this far above where it hangs (arm lengths),
HIP_INSET = 0.03                      # ... just inside the body's outline there; the elbow points out
POSE_A = {"wave": 5.0, "hip": 6.0}
POSE_B = {"wave": 8.5, "hip": -9.0}
BEND_PAST = 15.0                      # degrees B goes past the hip and wave poses at its ends
# The most one key may be from the next. The Live2D-format copy blends between keys in straight
# lines, so close keys keep a turning arm its length; on the device the shoulder turns exactly.
SWING_STEP = math.radians(12.5)
BEND_STEP = math.radians(10.0)
RIGID_TOLERANCE = 0.5       # pixels: a movement this close to a rotation is one


def fit_rotation(points_, moved):
    """The rotation about some centre that best takes points_ to moved: (centre, angle, worst miss)."""
    n = float(len(points_))
    vx = sum(p[0] for p in points_) / n
    vy = sum(p[1] for p in points_) / n
    wx = sum(p[0] for p in moved) / n
    wy = sum(p[1] for p in moved) / n
    cos_sum = sum((a[0] - vx) * (b[0] - wx) + (a[1] - vy) * (b[1] - wy) for a, b in zip(points_, moved))
    sin_sum = sum((a[0] - vx) * (b[1] - wy) - (a[1] - vy) * (b[0] - wx) for a, b in zip(points_, moved))
    angle = math.atan2(sin_sum, cos_sum)
    c, s = math.cos(angle), math.sin(angle)
    tx, ty = wx - (c * vx - s * vy), wy - (s * vx + c * vy)
    det = 2.0 - 2.0 * c
    if det < 1e-9:
        return None, 0.0, 0.0
    centre = (((1 - c) * tx - s * ty) / det, (s * tx + (1 - c) * ty) / det)
    miss = max(math.hypot(c * a[0] - s * a[1] + tx - b[0], s * a[0] + c * a[1] + ty - b[1])
               for a, b in zip(points_, moved))
    return centre, angle, miss


def rotated(pts, centre, angle):
    c, s = math.cos(angle), math.sin(angle)
    cx, cy = centre
    return [(cx + c * (x - cx) - s * (y - cy), cy + s * (x - cx) + c * (y - cy)) for x, y in pts]


def arm_keys(knots, step):
    """Keys along a control (0 to 1) and their angles (radians), through the given (control value from
    -10 to 10, angle) knots in straight lines, no two keys more than step apart."""
    knots = sorted(knots)
    keys = []
    for (v0, a0), (v1, a1) in zip(knots, knots[1:]):
        count = max(1, int(math.ceil(abs(a1 - a0) / step - 1e-9)))
        keys += [((v0 + (v1 - v0) * i / count + 10.0) / 20.0, a0 + (a1 - a0) * i / count) for i in range(count)]
    return keys + [((knots[-1][0] + 10.0) / 20.0, knots[-1][1])]


def outward_angle(vector, outward):
    """A direction's angle from straight down, in degrees, outward positive (outward: +1 for an arm
    on the picture's left, whose outside is towards -x; -1 on the right)."""
    return outward * math.degrees(math.atan2(-vector[0], vector[1]))


def arm_poses(shoulder, elbow, wrist, outward, body_edge=None):
    """The arm's control knots: (A knots, B knots) as (value, rotation in radians) from the drawing.

    body_edge(y) gives the body's outer edge on this arm's side at height y, for the hand on the hip.
    """
    upper = (elbow[0] - shoulder[0], elbow[1] - shoulder[1])
    fore = (wrist[0] - elbow[0], wrist[1] - elbow[1])
    up0, fore0 = outward_angle(upper, outward), outward_angle(fore, outward)
    l_up, l_fore = math.hypot(*upper), math.hypot(*fore)
    length = l_up + l_fore
    # Hand on hip: the hand on the body's outline a little above where it hangs, the elbow pointing out
    # (two-bone reach: the elbow is where the upper arm and the forearm meet on the way to the hand).
    hip_y = wrist[1] - HIP_UP * length
    edge = body_edge(hip_y) if body_edge else None
    # No further out than the hand hangs: a cape or a wide coat makes the outline there too wide.
    edge = wrist[0] if edge is None or -outward * edge > -outward * wrist[0] else edge
    hip_x = edge + outward * HIP_INSET * length
    to_hip = (hip_x - shoulder[0], hip_y - shoulder[1])
    reach = min(math.hypot(*to_hip), 0.999 * length)
    cos_at_shoulder = (l_up ** 2 + reach ** 2 - l_fore ** 2) / (2 * l_up * reach)
    swing = math.degrees(math.acos(max(-1.0, min(1.0, cos_at_shoulder))))
    hip_upper = outward_angle(to_hip, outward) + swing          # the elbow on the outer side
    c, s = math.cos(math.radians(outward * (hip_upper - up0))), math.sin(math.radians(outward * (hip_upper - up0)))
    bent_elbow = (shoulder[0] + c * upper[0] - s * upper[1], shoulder[1] + s * upper[0] + c * upper[1])
    hip_fore = outward_angle((hip_x - bent_elbow[0], hip_y - bent_elbow[1]), outward)
    hip_bend = hip_fore - fore0 - (hip_upper - up0)
    wave_bend = WAVE_FORE - fore0 - (WAVE_UPPER - up0)
    a = [(-10.0, ARM_IN - up0), (0.0, 0.0), (POSE_A["wave"], WAVE_UPPER - up0), (POSE_A["hip"], hip_upper - up0),
         (10.0, max(ARM_UP, hip_upper + 5.0) - up0)]
    b = [(-10.0, hip_bend - BEND_PAST), (POSE_B["hip"], hip_bend), (0.0, 0.0), (POSE_B["wave"], wave_bend),
         (10.0, wave_bend + BEND_PAST)]
    rad = math.radians
    return [(v, rad(d)) for v, d in a], [(v, rad(d)) for v, d in b], {
        "drawn_upper_deg": round(up0, 1), "drawn_forearm_deg": round(fore0, 1),
        "hip_upper_deg": round(hip_upper, 1), "hip_bend_deg": round(hip_bend, 1), "wave_bend_deg": round(wave_bend, 1)}


def body_edge_finder(puppet, outward, band):
    """body_edge(y): the torso's outermost point on one side near height y (the torso as completed behind
    the arms), in the arm parts' coordinates, or None."""
    torso = next((n for n in _nodes(puppet["nodes"]) if n.get("type") == "Part"
                  and role_of(n.get("name", "")) == "torso"), None)
    if torso is None:
        return None
    pts = _pairs(torso["mesh"]["verts"])

    def edge(y):
        near = [x for x, vy in pts if abs(vy - y) <= band]
        if not near:
            return None
        return min(near) if outward > 0 else max(near)
    return edge


def resample(binding, old_axis, new_axis):
    """A binding's values at new key positions, by Inochi2D's own straight-line rule between the old ones."""
    def at(position):
        for i in range(len(old_axis) - 1):
            if position <= old_axis[i + 1] or i == len(old_axis) - 2:
                t = (position - old_axis[i]) / (old_axis[i + 1] - old_axis[i])
                t = min(max(t, 0.0), 1.0)
                a, b = binding["values"][i][0], binding["values"][i + 1][0]
                if isinstance(a, list):
                    return [[round((1 - t) * p[0] + t * q[0], 3), round((1 - t) * p[1] + t * q[1], 3)]
                            for p, q in zip(a, b)]
                return (1 - t) * a + t * b
    out = dict(binding)
    out["values"] = [[at(position)] for position in new_axis]
    out["isSet"] = [[True]] * len(new_axis)
    return out


def _deform(binding_list, uid):
    return next((b for b in binding_list if b["node"] == uid and b["param_name"] == "deform"), None)


def _end_offsets(binding, end):
    """The offsets a deform binding stores at one end of its control (0: start, -1: end)."""
    return binding["values"][end][0]


def refine_arms(puppet):
    """Each arm: a node at the shoulder that turns the whole arm, and the forearm turning about the
    elbow inside it. Returns a short report."""
    params = {p["name"]: p for p in puppet.get("param", [])}
    nodes = list(_nodes(puppet["nodes"]))
    parent_of = {id(c): n for n in nodes for c in (n.get("children") or [])}
    next_uuid = max([n["uuid"] for n in nodes] + [p.get("uuid", 0) for p in puppet.get("param", [])]) + 1
    done = {}
    for side in ("l", "r"):
        swing, bend = params.get("ParamArm%sA" % side.upper()), params.get("ParamArm%sB" % side.upper())
        upper = next((n for n in nodes if n.get("type") == "Part" and role_of(n.get("name", "")) == "arm_%s_up" % side), None)
        fore = next((n for n in nodes if n.get("type") == "Part" and role_of(n.get("name", "")) == "arm_%s_lo" % side), None)
        if not (swing and bend and upper and fore) or len(swing["axis_points"][0]) < 2:
            continue
        body = parent_of.get(id(upper))
        if body is None or parent_of.get(id(fore)) is not body:
            continue
        if any(abs(v) > 1e-9 for n in (upper, fore) for v in n["transform"]["trans"][:2]):
            continue                                   # the rigger leaves parts at their parent's origin
        up_swing, fore_swing = _deform(swing["bindings"], upper["uuid"]), _deform(swing["bindings"], fore["uuid"])
        fore_bend = _deform(bend["bindings"], fore["uuid"])
        if not (up_swing and fore_swing and fore_bend):
            continue
        up_pts = [tuple(v) for v in _pairs(upper["mesh"]["verts"])]
        fore_pts = [tuple(v) for v in _pairs(fore["mesh"]["verts"])]
        shoulder, swing_angle, miss = fit_rotation(up_pts, [(x + d[0], y + d[1]) for (x, y), d in
                                                            zip(up_pts, _end_offsets(up_swing, -1))])
        elbow, bend_angle, miss2 = fit_rotation(fore_pts, [(x + d[0], y + d[1]) for (x, y), d in
                                                           zip(fore_pts, _end_offsets(fore_bend, -1))])
        if shoulder is None or elbow is None or max(miss, miss2) > RIGID_TOLERANCE:
            continue                                   # not the rigger's rigid chain: leave it
        outward = 1.0 if swing_angle > 0 else -1.0

        # The node at the shoulder, holding the arm; the parts keep their points where they were.
        arm = {"uuid": next_uuid, "name": "arm_%s" % side, "type": "Node", "enabled": True, "zsort": 0.0,
               "transform": {"trans": [round(shoulder[0], 3), round(shoulder[1], 3), 0.0],
                             "rot": [0.0, 0.0, 0.0], "scale": [1.0, 1.0]},
               "lockToRoot": False, "pinToMesh": False, "children": []}
        next_uuid += 1
        children = body["children"]
        at = min(children.index(upper), children.index(fore))
        for n in sorted((upper, fore), key=children.index):
            children.remove(n)
            n["transform"]["trans"] = [round(-shoulder[0], 3), round(-shoulder[1], 3), 0.0]
            arm["children"].append(n)
        children.insert(at, arm)

        # The wrist: the forearm's point farthest from the elbow.
        wrist = max(fore_pts, key=lambda q: math.hypot(q[0] - elbow[0], q[1] - elbow[1]))
        band = 0.04 * (math.hypot(elbow[0] - shoulder[0], elbow[1] - shoulder[1]) +
                       math.hypot(wrist[0] - elbow[0], wrist[1] - elbow[1]))
        swing_knots, bend_knots, poses = arm_poses(shoulder, elbow, wrist, outward,
                                                   body_edge_finder(puppet, outward, band))

        # A: the node turns. Other parts the rigger moved with the arm keep their movement, at the new keys.
        keys = arm_keys(swing_knots, SWING_STEP)
        old_axis, new_axis = swing["axis_points"][0], [k for k, _ in keys]
        kept = [resample(b, old_axis, new_axis) for b in swing["bindings"]
                if b["node"] not in (upper["uuid"], fore["uuid"])]
        kept.append({"node": arm["uuid"], "param_name": "transform.r.z",
                     "values": [[round(outward * a, 6)] for _, a in keys],
                     "isSet": [[True]] * len(keys), "interpolate_mode": "Linear"})
        swing["bindings"], swing["axis_points"] = kept, [new_axis, [0.0]]

        # B: the forearm turns about the elbow, stored at keys close enough for straight lines between.
        keys = arm_keys(bend_knots, BEND_STEP)
        old_axis, new_axis = bend["axis_points"][0], [k for k, _ in keys]
        kept = [resample(b, old_axis, new_axis) for b in bend["bindings"] if b["node"] != fore["uuid"]]
        columns = []
        for _, a in keys:
            moved = rotated(fore_pts, elbow, outward * a)
            columns.append([[[round(mx - x, 3), round(my - y, 3)] for (x, y), (mx, my) in zip(fore_pts, moved)]])
        kept.append({"node": fore["uuid"], "param_name": "deform", "values": columns,
                     "isSet": [[True]] * len(keys), "interpolate_mode": "Linear"})
        bend["bindings"], bend["axis_points"] = kept, [new_axis, [0.0]]
        done[side] = dict(poses, shoulder=[round(v, 1) for v in shoulder], elbow=[round(v, 1) for v in elbow])
    return {"arms": done or "left as rigged"}


def _pairs(flat):
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


MOUTH_PARAM = "ParamMouthOpenY"
# Openness at which the open mouth is fully visible, and how visible the
# mouth line still is there.
MOUTH_SHOWN = 0.25
LINE_AT_SHOWN = 0.6


def refine_mouth(puppet, parts):
    """Rewrite the mouth control: the line fades as the open mouth grows from it."""
    params = {p["name"]: p for p in puppet.get("param", [])}
    param = params.get(MOUTH_PARAM)
    lips = next((p for p in parts if p["role"] == "mouth"), None)
    cavity = next((p for p in parts if p["role"] == "mouth_cavity"), None)
    if param is None or lips is None or cavity is None:
        return {"mouth": "left as rigged"}
    ours = {lips["node"]["uuid"], cavity["node"]["uuid"]}
    kept = [b for b in param["bindings"] if b["node"] not in ours]
    top = box(points(cavity))[1]
    keys = (0.0, MOUTH_SHOWN, 1.0)
    shut = [[0.0, round(top - y, 3)] for _, y in points(cavity)]

    def deform(node, columns):
        return {"node": node["uuid"], "param_name": "deform", "values": [[c] for c in columns],
                "isSet": [[True]] * len(columns), "interpolate_mode": "Linear"}

    def opacity(node, values):
        return {"node": node["uuid"], "param_name": "opacity", "values": [[v] for v in values],
                "isSet": [[True]] * len(values), "interpolate_mode": "Linear"}

    zeros = [[0.0, 0.0]] * len(shut)
    kept.append(deform(cavity["node"], [shut, [[0.0, round(dy * (1 - MOUTH_SHOWN), 3)] for _, dy in shut], zeros]))
    kept.append(opacity(cavity["node"], [0.0, 1.0, 1.0]))
    kept.append(opacity(lips["node"], [1.0, LINE_AT_SHOWN, 0.0]))
    param["axis_points"] = [list(keys), [0.0]]
    param["bindings"] = kept
    return {"mouth": "line fades, open mouth grows from it"}


def finish(data):
    """Refine a rigger puppet file. Returns (new file bytes, report)."""
    puppet, rest = read_puppet(data)
    report = refine(puppet, rest)
    if isinstance(report.get("arms"), dict):
        rest = round_elbows(puppet, rest, report["arms"])
    return write_puppet(puppet, rest), report


TEXTURES = b"TEX_SECT"
ELBOW_MARGIN = 1.15         # the round end's radius, as a share of half the arm's width at the elbow


def read_texture_section(rest):
    """[(encoding, bytes)] and whatever follows the texture section."""
    if rest[:8] != TEXTURES:
        raise ValueError("the puppet has no texture section")
    count = struct.unpack(">I", rest[8:12])[0]
    pos, out = 12, []
    for _ in range(count):
        length = struct.unpack(">I", rest[pos:pos + 4])[0]
        out.append((rest[pos + 4], rest[pos + 5:pos + 5 + length]))
        pos += 5 + length
    return out, rest[pos:]


def write_texture_section(textures, after):
    out = [TEXTURES, struct.pack(">I", len(textures))]
    for encoding, data in textures:
        out += [struct.pack(">I", len(data)), bytes([encoding]), data]
    return b"".join(out) + after


def round_elbows(puppet, rest, arms):
    """Round each arm piece off at the elbow, as Live2D artists cut a joint.

    The rigger cuts an arm in two with a wide overlap, so each piece reaches a fifth of the arm's
    length past the elbow; once the elbow bends far, those ends stick out past each other in a cross.
    Each piece keeps its own side of the elbow and a disc of the elbow's own pixels, the same disc in
    both, so the joint stays closed however far it bends and the picture at rest is unchanged.
    """
    import io
    import numpy as np
    from PIL import Image
    textures, after = read_texture_section(rest)
    by_name = {role_of(n.get("name", "")): n for n in _nodes(puppet["nodes"]) if n.get("type") == "Part"}
    for side, joints in arms.items():
        upper, fore = by_name.get("arm_%s_up" % side), by_name.get("arm_%s_lo" % side)
        if upper is None or fore is None or upper["textures"][0] == fore["textures"][0]:
            continue
        shoulder, elbow = np.array(joints["shoulder"]), np.array(joints["elbow"])
        along = (elbow - shoulder) / max(np.linalg.norm(elbow - shoulder), 1e-6)
        pictures = {}
        for node, keep in ((upper, -1.0), (fore, 1.0)):
            index = node["textures"][0]
            encoding, data = textures[index]
            if encoding != 0:
                break                                   # only PNG textures are rewritten
            with Image.open(io.BytesIO(data)) as im:
                rgba = np.asarray(im.convert("RGBA")).copy()
            verts = np.array(node["mesh"]["verts"], float).reshape(-1, 2)
            uvs = np.array(node["mesh"]["uvs"], float).reshape(-1, 2) * [rgba.shape[1], rgba.shape[0]]
            scale, offset = [], []
            for axis in (0, 1):                         # texture pixels = scale * points + offset
                a, b = np.polyfit(verts[:, axis], uvs[:, axis], 1)
                scale.append(a)
                offset.append(b)
            if min(abs(v) for v in scale) < 1e-9:
                break
            ys, xs = np.mgrid[0:rgba.shape[0], 0:rgba.shape[1]]
            px = (xs + 0.5 - offset[0]) / scale[0]
            py = (ys + 0.5 - offset[1]) / scale[1]
            past = (px - elbow[0]) * along[0] + (py - elbow[1]) * along[1]
            near = np.hypot(px - elbow[0], py - elbow[1])
            pictures[index] = (rgba, past, near, keep)
        if len(pictures) != 2:
            continue
        # Half the arm's width at the elbow, measured across the arm in the upper piece's picture.
        rgba, past, near, _ = pictures[upper["textures"][0]]
        # (the farthest drawn pixel from the elbow along the line across it: the pivot sits mid-arm)
        across = (np.abs(past) <= 1.5) & (rgba[..., 3] > 127)
        half = float(near[across].max()) if across.any() else 0.0
        radius = max(2.0, ELBOW_MARGIN * half)
        for index, (rgba, past, near, keep) in pictures.items():
            gone = (past * keep < 0) & (near > radius)
            rgba[gone] = 0
            out = io.BytesIO()
            Image.fromarray(rgba, "RGBA").save(out, "PNG", optimize=True)
            textures[index] = (0, out.getvalue())
        arms[side]["elbow_radius"] = round(radius, 1)
    return write_texture_section(textures, after)


def _nodes(node):
    yield node
    for child in node.get("children") or []:
        yield from _nodes(child)


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    with open(argv[1], "rb") as f:
        data = f.read()
    try:
        data, report = finish(data)
    except (ValueError, StopIteration) as error:
        print("cannot refine this puppet: %s" % error, file=sys.stderr)
        return 1
    with open(argv[2], "wb") as f:
        f.write(data)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
