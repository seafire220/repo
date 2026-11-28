"""Whole-body movement for 3D (VRM) avatars, recorded with a phone (docs/26-animation-director.md, "The whole
body, for 3D avatars").

The phone's browser runs MediaPipe's pose model, which gives 33 points on the body in metres, centred between
the hips (its "world landmarks"), with how sure it is that each is seen, and where each is in the picture. Here
each frame's points become the turns of the avatar's bones in VRM's normalised skeleton (x towards the avatar's
left, y up, z towards the viewer; no bone turned in the T-pose), and the frames a VRM Animation clip (.vrma)
that the stage plays like any other (stage/src/vrm/anim.rs). Limbs are worked out from directions, not
lengths, so the movement fits any avatar.

Only what was seen is kept (the failsafes):

  * a part of the body seen for less than half the time is left out of the clip, and the avatar's own
    movement shows there (its idle loop). A recording without the hips and legs in the picture becomes an
    upper-body clip; one without the face leaves the head to the avatar;
  * the frames at the start and end where the shoulders are not seen are cut off;
  * in a part that is kept, a gap of up to BRIDGE seconds is bridged smoothly; a longer one eases that part
    to a resting pose and back;
  * too little of the body (the shoulders unseen for most of the time) is refused, with what to change;
  * the points are smoothed (without lag: a recording is smoothed both ways), elbows and knees bend only the
    way they can and no further than they can, and hands, feet, the head and the waist turn within limits;
  * the hips go down when the knees bend, so the feet stay on the floor (only when both legs were seen).
"""
import json
import math
import struct

import numpy as np

# MediaPipe's pose landmarks: the person's own left and right.
POINTS = {
    "nose": 0, "left_eye_inner": 1, "left_eye": 2, "left_eye_outer": 3, "right_eye_inner": 4, "right_eye": 5,
    "right_eye_outer": 6, "left_ear": 7, "right_ear": 8, "mouth_left": 9, "mouth_right": 10,
    "left_shoulder": 11, "right_shoulder": 12, "left_elbow": 13, "right_elbow": 14, "left_wrist": 15,
    "right_wrist": 16, "left_pinky": 17, "right_pinky": 18, "left_index": 19, "right_index": 20,
    "left_thumb": 21, "right_thumb": 22, "left_hip": 23, "right_hip": 24, "left_knee": 25, "right_knee": 26,
    "left_ankle": 27, "right_ankle": 28, "left_heel": 29, "right_heel": 30, "left_foot_index": 31,
    "right_foot_index": 32,
}
COUNT = 33
MIRROR = list(range(COUNT))
for _a, _b in ((1, 4), (2, 5), (3, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16), (17, 18), (19, 20),
               (21, 22), (23, 24), (25, 26), (27, 28), (29, 30), (31, 32)):
    MIRROR[_a], MIRROR[_b] = _b, _a

SEEN = 0.5          # how sure MediaPipe must be that a point is seen
INSIDE = 0.03       # how far outside the picture (a share of it) a point may be and still count
KEEP = 0.5          # a part is kept when seen in at least this share of the frames
BRIDGE = 0.6        # seconds: a gap this short is bridged
EASE = 0.3          # seconds to ease a part to rest, and back, over a longer gap
MIN_SECONDS = 0.5
RATE = 30.0         # frames a second in the clip
SMOOTH = 0.07       # seconds: how widely the points are smoothed
LIMITS = {"elbow": 150.0, "knee": 160.0, "hand": 60.0, "foot": 45.0, "head": 75.0, "waist": 60.0}

# The parts of the body, the points each needs seen, and what the owner is told about them.
PARTS = {
    "shoulders": ("left_shoulder", "right_shoulder"),
    "hips": ("left_hip", "right_hip"),
    "head": ("nose", "left_eye", "right_eye"),
    "left_arm": ("left_shoulder", "left_elbow", "left_wrist"),
    "right_arm": ("right_shoulder", "right_elbow", "right_wrist"),
    "left_hand": ("left_wrist", "left_index", "left_pinky"),
    "right_hand": ("right_wrist", "right_index", "right_pinky"),
    "left_leg": ("left_hip", "left_knee", "left_ankle"),
    "right_leg": ("right_hip", "right_knee", "right_ankle"),
    "left_foot": ("left_ankle", "left_heel", "left_foot_index"),
    "right_foot": ("right_ankle", "right_heel", "right_foot_index"),
}
SAID = {"shoulders": "upper body", "hips": "waist", "head": "head", "left_arm": "left arm",
        "right_arm": "right arm", "left_hand": "left hand", "right_hand": "right hand", "left_leg": "left leg",
        "right_leg": "right leg", "left_foot": "left foot", "right_foot": "right foot"}
# The bones each part moves.
BONES_OF = {
    "shoulders": ("spine", "chest", "upperChest"),
    "hips": ("hips",),
    "head": ("neck", "head"),
    "left_arm": ("leftShoulder", "leftUpperArm", "leftLowerArm"),
    "right_arm": ("rightShoulder", "rightUpperArm", "rightLowerArm"),
    "left_hand": ("leftHand",),
    "right_hand": ("rightHand",),
    "left_leg": ("leftUpperLeg", "leftLowerLeg"),
    "right_leg": ("rightUpperLeg", "rightLowerLeg"),
    "left_foot": ("leftFoot",),
    "right_foot": ("rightFoot",),
}

# The reference skeleton the clip is written on: VRM's T-pose, the same as stage/tools/vrma_kit.py's.
SKELETON = [
    ("hips", None, (0.0, 0.880, 0.0)),
    ("spine", "hips", (0.0, 0.940, 0.0)),
    ("chest", "spine", (0.0, 1.040, 0.0)),
    ("upperChest", "chest", (0.0, 1.140, 0.0)),
    ("neck", "upperChest", (0.0, 1.270, -0.010)),
    ("head", "neck", (0.0, 1.340, 0.0)),
]
for _side, _s in (("left", 1.0), ("right", -1.0)):
    SKELETON += [
        (_side + "Shoulder", "upperChest", (_s * 0.020, 1.240, -0.010)),
        (_side + "UpperArm", _side + "Shoulder", (_s * 0.105, 1.240, -0.010)),
        (_side + "LowerArm", _side + "UpperArm", (_s * 0.335, 1.240, -0.010)),
        (_side + "Hand", _side + "LowerArm", (_s * 0.555, 1.240, -0.010)),
        (_side + "UpperLeg", "hips", (_s * 0.075, 0.830, 0.0)),
        (_side + "LowerLeg", _side + "UpperLeg", (_s * 0.075, 0.450, 0.0)),
        (_side + "Foot", _side + "LowerLeg", (_s * 0.075, 0.065, 0.0)),
        (_side + "Toes", _side + "Foot", (_s * 0.075, 0.015, 0.120)),
    ]
REST = {name: np.array(pos) for name, _, pos in SKELETON}
PARENT = {name: parent for name, parent, _ in SKELETON}
ORDER = [name for name, _, _ in SKELETON]

# Where MediaPipe's points sit on that skeleton (the bone each is on, and how far from the bone's start), for
# the turns at rest and for tests. The right side mirrors the left.
MARKS = {
    "nose": ("head", (0.0, 0.035, 0.105)),
    "left_eye_inner": ("head", (0.015, 0.070, 0.085)),
    "left_eye": ("head", (0.032, 0.070, 0.080)),
    "left_eye_outer": ("head", (0.048, 0.070, 0.070)),
    "left_ear": ("head", (0.075, 0.055, 0.0)),
    "mouth_left": ("head", (0.025, 0.0, 0.090)),
    "left_shoulder": ("leftUpperArm", (0.0, 0.0, 0.0)),
    "left_elbow": ("leftLowerArm", (0.0, 0.0, 0.0)),
    "left_wrist": ("leftHand", (0.0, 0.0, 0.0)),
    "left_pinky": ("leftHand", (0.080, 0.0, -0.030)),
    "left_index": ("leftHand", (0.090, 0.0, 0.020)),
    "left_thumb": ("leftHand", (0.040, -0.010, 0.045)),
    "left_hip": ("leftUpperLeg", (0.0, 0.0, 0.0)),
    "left_knee": ("leftLowerLeg", (0.0, 0.0, 0.0)),
    "left_ankle": ("leftFoot", (0.0, 0.0, 0.0)),
    "left_heel": ("leftFoot", (0.0, -0.045, -0.050)),
    "left_foot_index": ("leftToes", (0.0, -0.010, 0.060)),
}
for _name, (_bone, _off) in list(MARKS.items()):
    if _name.startswith("left_") or _name == "mouth_left":
        _other = _name.replace("left", "right")
        MARKS[_other] = (_bone.replace("left", "right"), (-_off[0], _off[1], _off[2]))

# A limb's direction at rest, and the axis its middle joint bends about (elbows: the forearm swings forward
# in the T-pose, palms down; knees: the shin swings back).
HINGE_AT_REST = {"leftArm": (1.0, 0.0, 0.0, 0.0, -1.0, 0.0), "rightArm": (-1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
                 "leftLeg": (0.0, -1.0, 0.0, 1.0, 0.0, 0.0), "rightLeg": (0.0, -1.0, 0.0, 1.0, 0.0, 0.0)}
# Resting poses for parts eased out over a long gap: arms down by the sides, a little bent.
RESTING = {"leftUpperArm": ((0.0, 0.0, 1.0), -72.0), "rightUpperArm": ((0.0, 0.0, 1.0), 72.0),
           "leftLowerArm": ((0.0, -1.0, 0.0), 10.0), "rightLowerArm": ((0.0, 1.0, 0.0), 10.0)}


class BodyError(ValueError):
    pass


# ---------------------------------------------------------------- rotations (matrices; quaternions x, y, z, w)

def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v * 0.0


def _basis(primary, secondary):
    """A rotation matrix whose first column is `primary` and second `secondary` (made square to it)."""
    e1 = _unit(primary)
    e2 = _unit(secondary - np.dot(secondary, e1) * e1)
    if not e2.any():
        e2 = _unit(np.cross(e1, (0.0, 0.0, 1.0) if abs(e1[2]) < 0.9 else (1.0, 0.0, 0.0)))
    return np.column_stack((e1, e2, np.cross(e1, e2)))


def _axis_angle(axis, degrees):
    x, y, z = _unit(np.asarray(axis, float))
    a = math.radians(degrees)
    c, s, t = math.cos(a), math.sin(a), 1.0 - math.cos(a)
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


def quat(m):
    """A rotation matrix as a quaternion (x, y, z, w), w >= 0."""
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = ((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = (0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s)
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = ((m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s)
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = ((m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s)
    q = np.array(q) / np.linalg.norm(q)
    return q if q[3] >= 0 else -q


def matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def angle(m):
    """How far a rotation matrix turns, in degrees."""
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(m) - 1.0) / 2.0))))


def _power(m, t):
    """A share t of a rotation (its axis, t times its angle)."""
    q = quat(m)
    a = 2.0 * math.acos(max(-1.0, min(1.0, q[3])))
    if a < 1e-6:
        return np.eye(3)
    return _axis_angle(q[:3] / math.sin(a / 2.0), math.degrees(a) * t)


def _limit(m, degrees):
    a = angle(m)
    return m if a <= degrees else _power(m, degrees / a)


def _slerp(a, b, t):
    if np.dot(a, b) < 0:
        b = -b
    d = float(np.clip(np.dot(a, b), -1.0, 1.0))
    if d > 0.9995:
        q = a + (b - a) * t
    else:
        th = math.acos(d)
        q = (math.sin((1 - t) * th) * a + math.sin(t * th) * b) / math.sin(th)
    q = q / np.linalg.norm(q)
    return q if q[3] >= 0 else -q


# ---------------------------------------------------------------- the points at rest, and from a pose

def forward(local, hips=(0.0, 0.0, 0.0)):
    """World turns (matrices) and positions of the skeleton's bones for local turns (bone -> matrix)."""
    world_rot, world_pos = {}, {}
    for name in ORDER:
        parent = PARENT[name]
        m = local.get(name, np.eye(3))
        if parent is None:
            world_rot[name] = m
            world_pos[name] = REST[name] + np.asarray(hips, float)
        else:
            world_rot[name] = world_rot[parent] @ m
            world_pos[name] = world_pos[parent] + world_rot[parent] @ (REST[name] - REST[parent])
    return world_rot, world_pos


def marks(local, hips=(0.0, 0.0, 0.0)):
    """MediaPipe's 33 points (in the avatar's frame, x left, y up, z to the viewer) for a pose."""
    world_rot, world_pos = forward(local, hips)
    out = np.zeros((COUNT, 3))
    for name, index in POINTS.items():
        bone, off = MARKS[name]
        out[index] = world_pos[bone] + world_rot[bone] @ np.asarray(off, float)
    return out


def _frames(p):
    """The frames the body's parts are measured in, from 33 points in the avatar's frame."""
    def pt(name):
        return p[POINTS[name]]
    mid_hip = (pt("left_hip") + pt("right_hip")) / 2
    mid_sh = (pt("left_shoulder") + pt("right_shoulder")) / 2
    up = mid_sh - mid_hip
    ears = (pt("left_ear") + pt("right_ear")) / 2
    side = pt("left_ear") - pt("right_ear")
    out = {
        "hips": _basis(pt("left_hip") - pt("right_hip"), up),
        "shoulders": _basis(pt("left_shoulder") - pt("right_shoulder"), up),
        "shoulders_level": _basis(pt("left_shoulder") - pt("right_shoulder"), np.array([0.0, 1.0, 0.0])),
        "head": _basis(side, np.cross(pt("nose") - ears, side)),
    }
    for s in ("left", "right"):
        wrist = pt(s + "_wrist")
        out[s + "_hand"] = _basis((pt(s + "_index") + pt(s + "_pinky")) / 2 - wrist,
                                  pt(s + "_index") - pt(s + "_pinky"))
        out[s + "_foot"] = _basis(pt(s + "_foot_index") - pt(s + "_heel"), np.array([1.0, 0.0, 0.0]))
    return out


AT_REST = _frames(marks({}))


# ---------------------------------------------------------------- one frame's points to bone turns

def _limb(a_pt, b_pt, c_pt, rest, limit, hinge_before):
    """The turns of a two-bone limb: the first bone's world turn, the second's turn about its hinge (local),
    and the hinge (world) for the next frame.

    The hinge (the axis the elbow or knee bends about) is only as sure as the bend is large: a nearly
    straight limb keeps the last frame's, and a little bent one moves towards the new one only a little, so
    noise cannot twist the limb round. A hinge turned right round from the last frame's is a limb bending
    the wrong way (or noise): it keeps the last hinge and stays straight."""
    a0 = np.array(rest[:3])
    n0 = np.array(rest[3:])
    a = _unit(b_pt - a_pt)
    d = _unit(c_pt - b_pt)
    raw = np.cross(a, d)
    size = float(np.linalg.norm(raw))
    bend = math.degrees(math.atan2(size, float(np.dot(a, d))))
    kept = None
    if hinge_before is not None:
        kept = _unit(hinge_before - np.dot(hinge_before, a) * a)
        if not kept.any():
            kept = None
    if kept is None:
        if size < math.sin(math.radians(12.0)):
            n = _unit(_swing(a0, a) @ n0)          # no hinge yet: as if it swung straight from rest
        else:
            n = raw / size
    elif size < 1e-9 or float(np.dot(raw / size, kept)) < -0.17:
        n = kept
    else:
        t = min(max((bend - 10.0) / 35.0, 0.0), 1.0)
        trust = t * t * (3.0 - 2.0 * t)
        n = _unit(kept * (1.0 - trust) + (raw / size) * trust)
        n = _unit(n - np.dot(n, a) * a)
    bend = math.degrees(math.atan2(float(np.dot(np.cross(a, d), n)), float(np.dot(a, d))))
    bend = min(max(bend, 0.0), limit)
    upper = _basis(a, n) @ _basis(a0, n0).T
    lower = _axis_angle(n0, bend)
    return upper, lower, n


def _swing(a, b):
    """The smallest turn taking direction a to direction b."""
    a, b = _unit(a), _unit(b)
    axis = np.cross(a, b)
    s = float(np.linalg.norm(axis))
    if s < 1e-8:
        return np.eye(3) if np.dot(a, b) > 0 else _axis_angle(_basis(a, np.array([0.0, 1.0, 0.0]))[:, 1], 180.0)
    return _axis_angle(axis / s, math.degrees(math.atan2(s, float(np.dot(a, b)))))


def solve(p, scope, hinges):
    """Local turns (bone -> matrix) for one frame's 33 points in the avatar's frame. `scope` names the parts
    kept; `hinges` carries each limb's hinge from frame to frame."""
    f = _frames(p)
    local = {}
    world = {}

    def pt(name):
        return p[POINTS[name]]

    # The waist: the hips' turn from the hip line and the spine; the chest's from the shoulder line. Without
    # the hips seen, the chest turns and leans sideways by the shoulders alone.
    if "hips" in scope:
        hips = f["hips"] @ AT_REST["hips"].T
        chest = f["shoulders"] @ AT_REST["shoulders"].T
    else:
        hips = np.eye(3)
        chest = f["shoulders_level"] @ AT_REST["shoulders_level"].T
    waist = _limit(hips.T @ chest, LIMITS["waist"])
    third = _power(waist, 1.0 / 3.0)
    local["hips"] = hips
    world["hips"] = hips
    parent = hips
    for bone in ("spine", "chest", "upperChest"):
        local[bone] = third
        parent = parent @ third
        world[bone] = parent
    # The head, from the ears and nose: shared between the neck (two fifths) and the head.
    head_world = f["head"] @ AT_REST["head"].T
    rel = _limit(world["upperChest"].T @ head_world, LIMITS["head"])
    local["neck"] = _power(rel, 0.4)
    local["head"] = _power(rel, 0.6)
    # The arms and hands.
    for s in ("left", "right"):
        local[s + "Shoulder"] = np.eye(3)
        shoulder_world = world["upperChest"]
        upper, lower, hinges[s + "Arm"] = _limb(pt(s + "_shoulder"), pt(s + "_elbow"), pt(s + "_wrist"),
                                                HINGE_AT_REST[s + "Arm"], LIMITS["elbow"], hinges.get(s + "Arm"))
        local[s + "UpperArm"] = shoulder_world.T @ upper
        local[s + "LowerArm"] = lower
        hand = f[s + "_hand"] @ AT_REST[s + "_hand"].T
        local[s + "Hand"] = _limit((upper @ lower).T @ hand, LIMITS["hand"])
    # The legs and feet.
    for s in ("left", "right"):
        upper, lower, hinges[s + "Leg"] = _limb(pt(s + "_hip"), pt(s + "_knee"), pt(s + "_ankle"),
                                                HINGE_AT_REST[s + "Leg"], LIMITS["knee"], hinges.get(s + "Leg"))
        local[s + "UpperLeg"] = hips.T @ upper
        local[s + "LowerLeg"] = lower
        foot_frame = _basis(pt(s + "_foot_index") - pt(s + "_heel"), hinges[s + "Leg"])
        foot = foot_frame @ _basis(AT_REST[s + "_foot"][:, 0], np.array([1.0, 0.0, 0.0])).T
        local[s + "Foot"] = _limit((upper @ lower).T @ foot, LIMITS["foot"])
    return local


# ---------------------------------------------------------------- the recording

def frames_from_payload(body):
    """The body points of each frame of what a phone sends (phone_record.frames_from_payload's frames, each
    with "body": [[x, y, z, visibility, picture x, picture y] x 33] or null; x, y, z in metres from between
    the hips, as MediaPipe's world landmarks; picture x, y as fractions of the picture). Returns
    (points [frames, 33, 3] in the avatar's frame, seen [frames, 33]); a frame without a body is unseen."""
    raw = body.get("frames") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        raise BodyError("send the landmarks as an object with frames")
    points = np.zeros((len(raw), COUNT, 3))
    seen = np.zeros((len(raw), COUNT), bool)
    found = 0
    for k, frame in enumerate(raw):
        rows = frame.get("body") if isinstance(frame, dict) else None
        if rows is None:
            continue
        if not isinstance(rows, list) or len(rows) != COUNT:
            raise BodyError("frame %d: the body has %d points, not %d" % (k, len(rows) if isinstance(rows, list) else 0, COUNT))
        try:
            a = np.array(rows, float)
        except (TypeError, ValueError):
            raise BodyError("frame %d: the body's points are not numbers" % k)
        if a.shape != (COUNT, 6) or not np.isfinite(a).all() or np.abs(a[:, :3]).max() > 5.0 \
                or a[:, 3].min() < 0.0 or a[:, 3].max() > 1.0 or a[:, 4:].min() < -2.0 or a[:, 4:].max() > 3.0:
            raise BodyError("frame %d: the body's points are out of range" % k)
        # MediaPipe: x to the picture's right (the person's left, facing the camera), y down, z away from the
        # camera. The avatar: x to its left, y up, z towards the viewer.
        points[k] = a[:, :3] * (1.0, -1.0, -1.0)
        inside = (a[:, 4] >= -INSIDE) & (a[:, 4] <= 1 + INSIDE) & (a[:, 5] >= -INSIDE) & (a[:, 5] <= 1 + INSIDE)
        seen[k] = (a[:, 3] >= SEEN) & inside
        found += 1
    if not found:
        raise BodyError("no body was found in the recording")
    return points, seen


def _smooth_points(points, seen, fps):
    """Each point smoothed over time both ways (no lag), the frames it was seen in counting most."""
    sigma = max(SMOOTH * fps, 0.5)
    reach = int(math.ceil(2.5 * sigma))
    offsets = np.arange(-reach, reach + 1)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    weight = np.where(seen, 1.0, 0.05)
    n = len(points)
    out = np.empty_like(points)
    for k in range(n):
        idx = np.clip(k + offsets, 0, n - 1)
        w = kernel[:, None] * weight[idx]
        out[k] = (points[idx] * w[:, :, None]).sum(axis=0) / np.maximum(w.sum(axis=0), 1e-9)[:, None]
    return out


def _runs(flags):
    """(start, end) of each run of True."""
    out, start = [], None
    for k, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = k
        elif not f and start is not None:
            out.append((start, k))
            start = None
    return out


def make(body, mirror=False):
    """A recording to a clip. Returns (the .vrma file's bytes, a report of what was kept). Raises BodyError
    saying what to change when too little of the body was seen."""
    fps = float(body.get("fps") or 0)
    if not 2.0 <= fps <= 30.0:
        raise BodyError("fps is not a number from 2 to 30")
    points, seen = frames_from_payload(body)
    if mirror:
        points = points[:, MIRROR] * (-1.0, 1.0, 1.0)
        seen = seen[:, MIRROR]
    part_seen = {part: seen[:, [POINTS[n] for n in names]].all(axis=1) for part, names in PARTS.items()}
    # Cut the frames at either end where the shoulders are not seen.
    seen_frames = np.nonzero(part_seen["shoulders"])[0]
    total = len(points)
    if len(seen_frames) < max(2, 0.5 * total):
        raise BodyError("your shoulders were out of the picture (or hidden) most of the time: film from a little "
                        "further away, with at least your upper body in the picture")
    first, last = int(seen_frames[0]), int(seen_frames[-1]) + 1
    points, seen = points[first:last], seen[first:last]
    part_seen = {k: v[first:last] for k, v in part_seen.items()}
    n = len(points)
    if (n - 1) / fps < MIN_SECONDS:
        raise BodyError("the movement is too short: at least half a second is needed")

    report = {"kept": [], "left_out": [], "bridged": [], "eased": [], "trimmed": round((total - n) / fps, 1)}
    shares = {part: float(flags.mean()) for part, flags in part_seen.items()}
    scope = set()
    for part in ("shoulders", "hips", "head", "left_arm", "right_arm", "left_leg", "right_leg", "left_hand",
                 "right_hand", "left_foot", "right_foot"):
        needs = {"left_leg": "hips", "right_leg": "hips", "left_hand": "left_arm", "right_hand": "right_arm",
                 "left_foot": "left_leg", "right_foot": "right_leg"}.get(part)
        if shares[part] < KEEP:
            why = "not in the picture" if shares[part] < 0.05 else "seen only %d%% of the time" % round(100 * shares[part])
            report["left_out"].append({"part": SAID[part], "why": why})
        elif needs is not None and needs not in scope:
            report["left_out"].append({"part": SAID[part], "why": "the %s was not seen enough" % SAID[needs]})
        else:
            scope.add(part)
            report["kept"].append(SAID[part])
    report["scope"] = ("whole body" if {"hips", "left_leg", "right_leg"} <= scope else "upper body")

    smooth = _smooth_points(points, seen, fps)
    hinges = {}
    turns = []
    for k in range(n):
        turns.append({b: quat(m) for b, m in solve(smooth[k], scope, hinges).items()})

    # The parts kept: bridge short gaps; ease long ones to rest and back.
    bones = []
    for part in sorted(scope):
        bones += BONES_OF[part]
        gaps = _runs(~part_seen[part])
        for start, end in gaps:
            seconds = (end - start) / fps
            if seconds <= BRIDGE and start > 0 and end < n:
                for k in range(start, end):
                    t = (k - start + 1) / (end - start + 1)
                    for b in BONES_OF[part]:
                        turns[k][b] = _slerp(turns[start - 1][b], turns[end][b], t)
                report["bridged"].append({"part": SAID[part], "seconds": round(seconds, 1)})
            else:
                ease = max(1, int(round(EASE * fps)))
                for b in BONES_OF[part]:
                    rest = quat(_axis_angle(*RESTING[b])) if b in RESTING else np.array([0.0, 0.0, 0.0, 1.0])
                    before = turns[start - 1][b] if start > 0 else rest
                    after = turns[end][b] if end < n else rest
                    for k in range(start, end):
                        t_in = min(1.0, (k - start + 1) / ease) if start > 0 else 1.0
                        t_out = min(1.0, (end - k) / ease) if end < n else 1.0
                        q = _slerp(before, rest, t_in)
                        turns[k][b] = _slerp(after, q, t_out)
                report["eased"].append({"part": SAID[part], "seconds": round(seconds, 1)})

    # The hips go down as the knees bend, so the feet stay on the floor.
    hips_offset = None
    if {"left_leg", "right_leg"} <= scope:
        rest_ankle = REST["leftFoot"][1]
        heights = []
        for k in range(n):
            local = {b: matrix(q) for b, q in turns[k].items()}
            _, pos = forward(local)
            heights.append(rest_ankle - min(pos["leftFoot"][1], pos["rightFoot"][1]))
        heights = np.array(heights)
        width = max(1, int(round(0.1 * fps)))
        smoothed = np.array([heights[max(0, k - width):k + width + 1].mean() for k in range(n)])
        hips_offset = np.clip(smoothed, -0.5, 0.05)

    data = _write(turns, bones, hips_offset, fps)
    report["seconds"] = round((n - 1) / fps, 2)
    report["bones"] = len(bones)
    return data, report


def _resample(turns, bones, hips_offset, fps):
    """Frames at RATE a second, turns eased between the recording's frames."""
    n = len(turns)
    duration = (n - 1) / fps
    count = int(math.floor(duration * RATE + 1e-6)) + 1
    times = [min(k / RATE, duration) for k in range(count)]
    tracks = {b: [] for b in bones}
    hips = []
    for t in times:
        f = t * fps
        i = min(int(math.floor(f)), n - 1)
        j = min(i + 1, n - 1)
        u = f - i
        for b in bones:
            tracks[b].append(_slerp(turns[i][b], turns[j][b], u))
        if hips_offset is not None:
            hips.append(hips_offset[i] * (1 - u) + hips_offset[j] * u)
    return times, tracks, hips


def _write(turns, bones, hips_offset, fps):
    """The clip as a VRM Animation file (glTF binary)."""
    times, tracks, hips = _resample(turns, bones, hips_offset, fps)
    nodes, index = [], {}
    for name, parent, pos in SKELETON:
        index[name] = len(nodes)
        local = REST[name] if parent is None else REST[name] - REST[parent]
        nodes.append({"name": name, "translation": [round(float(c), 5) for c in local]})
    for name, parent, _ in SKELETON:
        if parent is not None:
            nodes[index[parent]].setdefault("children", []).append(index[name])
    blob = bytearray()
    views, accessors, samplers, channels = [], [], [], []

    def accessor(values, kind, count, minmax=False):
        while len(blob) % 4:
            blob.append(0)
        offset = len(blob)
        blob.extend(struct.pack("<%df" % len(values), *values))
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(values) * 4})
        acc = {"bufferView": len(views) - 1, "componentType": 5126, "count": count, "type": kind}
        if minmax:
            acc["min"], acc["max"] = [min(values)], [max(values)]
        accessors.append(acc)
        return len(accessors) - 1

    time_acc = accessor(times, "SCALAR", len(times), minmax=True)
    for name in ORDER:
        if name not in tracks:
            continue
        qs = tracks[name]
        for k in range(1, len(qs)):             # each frame the nearest to the one before: no flips
            if float(np.dot(qs[k], qs[k - 1])) < 0:
                qs[k] = -qs[k]
        out = accessor([float(c) for q in qs for c in q], "VEC4", len(qs))
        samplers.append({"input": time_acc, "output": out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": index[name], "path": "rotation"}})
    if hips:
        values = [float(c) for h in hips for c in (0.0, REST["hips"][1] + h, 0.0)]
        out = accessor(values, "VEC3", len(hips))
        samplers.append({"input": time_acc, "output": out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": index["hips"], "path": "translation"}})
    doc = {
        "asset": {"version": "2.0", "generator": "companion body_motion"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "scene": 0,
        "scenes": [{"nodes": [index["hips"]]}],
        "nodes": nodes,
        "animations": [{"name": "recorded", "samplers": samplers, "channels": channels}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
        "extensions": {"VRMC_vrm_animation": {
            "specVersion": "1.0",
            "humanoid": {"humanBones": {name: {"node": index[name]} for name in ORDER}},
        }},
    }
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    blob.extend(b"\x00" * (-len(blob) % 4))
    total = 12 + 8 + len(text) + 8 + len(blob)
    return (struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(text), b"JSON") + text
            + struct.pack("<I4s", len(blob), b"BIN\x00") + bytes(blob))


def describe(report):
    """What was kept, in a sentence for the owner."""
    words = "Kept: %s." % ", ".join(report["kept"]) if report["kept"] else "Nothing was kept."
    if report["left_out"]:
        words += " Left to the avatar's own movement: %s." % "; ".join(
            "%s (%s)" % (o["part"], o["why"]) for o in report["left_out"])
    if report["bridged"]:
        words += " Smoothed over short gaps: %s." % ", ".join(sorted({b["part"] for b in report["bridged"]}))
    if report["eased"]:
        words += " Rested while out of sight: %s." % ", ".join(sorted({b["part"] for b in report["eased"]}))
    return words
