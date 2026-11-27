"""Gestures from a person's movement: MediaPipe's face and body landmarks to the stage's controls.

The same conversion serves both ways of recording one:
- Character Studio's `studio/record_motion.py` reads a video on a computer with MediaPipe for Python;
- a phone (phone_record.py) reads its own video in its browser with MediaPipe for the web, and sends the
  landmarks here.

The landmarks become the stage's controls: the head's turn, nod and tilt; where the eyes look; the brows,
smile, smiling eyes and mouth; the shoulders and the body's tilt; each arm at the shoulder and the elbow
(not the lean towards or away from the camera: one camera's depth is too unsure). They are smoothed (the
1-euro filter), measured from the pose at the start, eased in and out so the clip starts and ends at rest,
cut down to the keys that matter, and written as the motion library writes a clip. The character moves the
way the video shows it, side for side: a head turned towards the right of the picture turns the character
towards the right of its screen (`mirror_tracks` swaps them).
"""
import math

# Face landmarks (MediaPipe's 468-point face mesh): the eyes' outer corners, the nose tip, the cheeks'
# edges and the chin. "Right" and "left" are the picture's.
EYE_LEFT, EYE_RIGHT, NOSE, CHEEK_LEFT, CHEEK_RIGHT, CHIN, BROW_MID = 33, 263, 1, 234, 454, 152, 9
FACE_POINTS = (EYE_LEFT, EYE_RIGHT, NOSE, CHEEK_LEFT, CHEEK_RIGHT, CHIN)
# Body landmarks (MediaPipe pose): the person's own right side shows on the picture's left.
POSE = {"nose": 0, "shoulder_l": 12, "elbow_l": 14, "wrist_l": 16, "shoulder_r": 11, "elbow_r": 13, "wrist_r": 15,
        "hip_l": 24, "hip_r": 23}
# The face blendshapes face_controls reads (MediaPipe's names).
BLENDSHAPES = ("eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft", "eyeLookOutRight", "eyeLookUpLeft",
               "eyeLookUpRight", "eyeLookDownLeft", "eyeLookDownRight", "browInnerUp", "browOuterUpLeft",
               "browOuterUpRight", "browDownLeft", "browDownRight", "mouthSmileLeft", "mouthSmileRight",
               "mouthFrownLeft", "mouthFrownRight", "eyeSquintLeft", "eyeSquintRight", "jawOpen", "eyeBlinkLeft",
               "eyeBlinkRight")

ONE_SIDED = {"mouth_open", "blink", "eye_smile", "cheek"}
QUIET = 0.08            # a track that never moves this far is left out
SIMPLIFY = 0.025        # keys are kept where leaving them out would move a track this much
EASE = 0.25             # seconds eased in at the start and out at the end
NEUTRAL = 0.3           # seconds at the start that give the person's resting pose
VISIBLE = 0.5           # how sure the body model must be that a joint is in the picture

# Controls that change sign, and pairs that swap, when left and right are swapped (mirror_tracks).
MIRROR_SIGN = {"head_yaw", "head_roll", "gaze_x", "body_yaw", "body_roll"}
MIRROR_SWAP = {"arm_left": "arm_right", "arm_left_b": "arm_right_b"}


# ----------------------------------------------------------------------------------------------------
# Landmarks to controls (plain functions of points, so they are tested without a video)
# ----------------------------------------------------------------------------------------------------

def _clamp(x, lo=-1.2, hi=1.2):
    return max(lo, min(hi, x))


def head_controls(face):
    """The head's turn, nod and tilt from face landmarks ({index: (x, y)} in picture coordinates, y down):
    turn from where the nose sits between the cheeks; nod from where it sits between the eyes and chin;
    tilt from the slope of the line through the eyes. Not yet measured from the rest pose."""
    (lx, ly), (rx, ry) = face[EYE_LEFT], face[EYE_RIGHT]
    nx, ny = face[NOSE]
    cl, cr = face[CHEEK_LEFT][0], face[CHEEK_RIGHT][0]
    half = max(1e-6, (cr - cl) / 2.0)
    yaw = (nx - (cl + cr) / 2.0) / half                      # nose to the picture's right: turned right
    eyes_y = (ly + ry) / 2.0
    pitch = (ny - eyes_y) / max(1e-6, face[CHIN][1] - eyes_y)   # bigger looking down
    roll = math.degrees(math.atan2(ry - ly, rx - lx))       # clockwise in the picture is positive
    return {"head_yaw": 2.0 * yaw, "head_pitch": -5.0 * pitch, "head_roll": roll / 20.0}


def face_controls(blend):
    """Gaze, brows, smile, smiling eyes, mouth and blinks from MediaPipe's face blendshapes ({name: 0..1})."""
    b = lambda name: float(blend.get(name, 0.0))  # noqa: E731
    mean = lambda *names: sum(b(n) for n in names) / len(names)  # noqa: E731
    # The person's right eye shows on the picture's left: looking "in" with it is looking to the picture's right.
    gaze_x = mean("eyeLookInRight", "eyeLookOutLeft") - mean("eyeLookOutRight", "eyeLookInLeft")
    gaze_y = mean("eyeLookUpLeft", "eyeLookUpRight") - mean("eyeLookDownLeft", "eyeLookDownRight")
    return {
        "gaze_x": 1.5 * gaze_x,
        "gaze_y": 1.5 * gaze_y,
        "brows": b("browInnerUp") + mean("browOuterUpLeft", "browOuterUpRight") - mean("browDownLeft", "browDownRight"),
        "smile": 1.2 * (mean("mouthSmileLeft", "mouthSmileRight") - mean("mouthFrownLeft", "mouthFrownRight")),
        "eye_smile": mean("eyeSquintLeft", "eyeSquintRight"),
        "mouth_open": 1.2 * b("jawOpen"),
        "blink": mean("eyeBlinkLeft", "eyeBlinkRight"),
    }


def _arm(shoulder, elbow, wrist, outward, down=(0.0, 1.0)):
    """One arm: the whole arm at the shoulder (0 hanging, 1 straight out) and the forearm at the elbow
    (positive raised outwards and up, as in a wave; negative bent in towards the body, as for a hand on the
    hip). `outward` is -1 for the arm on the picture's left, 1 for the one on its right; `down` is the body's
    own downward direction (so leaning the whole body does not move the arms)."""
    ux, uy = elbow[0] - shoulder[0], elbow[1] - shoulder[1]
    fx, fy = wrist[0] - elbow[0], wrist[1] - elbow[1]
    # Into the body's frame: x along the shoulders, y down the body.
    dx, dy = down
    ux, uy = ux * dy - uy * dx, ux * dx + uy * dy
    fx, fy = fx * dy - fy * dx, fx * dx + fy * dy
    # The upper arm's angle from hanging straight down, away from the body positive.
    out = math.degrees(math.atan2(outward * ux, uy))
    bend = math.degrees(math.atan2(ux * fy - uy * fx, ux * fx + uy * fy))   # from the upper arm to the forearm
    bend = -bend if outward > 0 else bend
    return _clamp(out / 90.0), _clamp(bend / 120.0, -1.0, 1.0)


def shoulder_controls(pose):
    """The shoulders (raised or lowered) and the body's tilt, from the nose and shoulders ({name: (x, y, ...)},
    picture coordinates, y down), and the body's downward direction."""
    sl, sr = pose["shoulder_l"], pose["shoulder_r"]
    ax, ay = sr[0] - sl[0], sr[1] - sl[1]
    width = max(1e-6, math.hypot(ax, ay))
    # How far the nose is above the line through the shoulders, for their width: raised shoulders bring the
    # line up towards it. Measured across the line, so tilting the whole body does not count.
    above = (ax * (pose["nose"][1] - sl[1]) - ay * (pose["nose"][0] - sl[0])) / width / width
    down = (-ay / width, ax / width)
    return {"shoulders": 3.0 * above, "body_roll": math.degrees(math.atan2(ay, ax)) / 10.0}, down


def body_controls(pose):
    """Shoulders, the body's tilt, and the arms, from body landmarks ({name: (x, y, z)}, picture coordinates
    with y down). The lean towards or away from the camera is not read: one camera's depth is too unsure."""
    out, down = shoulder_controls(pose)
    a, b = _arm(pose["shoulder_l"], pose["elbow_l"], pose["wrist_l"], -1, down)
    out["arm_left"], out["arm_left_b"] = a, b
    a, b = _arm(pose["shoulder_r"], pose["elbow_r"], pose["wrist_r"], 1, down)
    out["arm_right"], out["arm_right_b"] = a, b
    return out


ARMS = ("nose", "shoulder_l", "shoulder_r", "elbow_l", "wrist_l", "elbow_r", "wrist_r")


def frame_controls(face=None, blend=None, pose=None):
    """One frame's controls from whatever was found in it: face points ({index: (x, y)}, picture coordinates),
    blendshapes ({name: score}) and body landmarks ({name: (x, y, z, visibility)}). The arms count only when
    the body model is sure they are in the picture; the shoulders count whenever they are found."""
    controls = {}
    if face:
        controls.update(head_controls(face))
    if blend:
        controls.update(face_controls(blend))
    if pose:
        seen = {name: p[:3] for name, p in pose.items() if p[3] > VISIBLE or name.startswith(("shoulder", "nose"))}
        if all(k in seen for k in ARMS):
            controls.update(body_controls(seen))
        elif all(k in seen for k in ("nose", "shoulder_l", "shoulder_r")):
            controls.update(shoulder_controls(seen)[0])
    return controls


# ----------------------------------------------------------------------------------------------------
# Frames to a clip
# ----------------------------------------------------------------------------------------------------

class OneEuro:
    """The 1-euro filter (Casiez, Roussel and Vogel, 2012): smooths jitter, keeps quick moves quick."""

    def __init__(self, min_cutoff=1.0, beta=0.5, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = None

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, dt):
        if self.x is None:
            self.x, self.dx = x, 0.0
            return x
        dx = (x - self.x) / dt
        a = self._alpha(self.d_cutoff, dt)
        self.dx += a * (dx - self.dx)
        a = self._alpha(self.min_cutoff + self.beta * abs(self.dx), dt)
        self.x += a * (x - self.x)
        return self.x


def _fill(values):
    """Missing readings (None) filled in from their neighbours."""
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return None
    out = list(values)
    for i in range(len(out)):
        if out[i] is None:
            before = max((k for k in known if k < i), default=None)
            after = min((k for k in known if k > i), default=None)
            if before is None:
                out[i] = out[after]
            elif after is None:
                out[i] = out[before]
            else:
                w = (i - before) / (after - before)
                out[i] = out[before] * (1 - w) + out[after] * w
    return out


def _simplify(points, epsilon):
    """Ramer-Douglas-Peucker: the keys a track needs to stay within `epsilon` of itself."""
    if len(points) < 3:
        return list(points)
    (t0, v0), (t1, v1) = points[0], points[-1]
    worst, at = 0.0, 0
    for i in range(1, len(points) - 1):
        t, v = points[i]
        line = v0 + (v1 - v0) * (t - t0) / max(1e-9, t1 - t0)
        if abs(v - line) > worst:
            worst, at = abs(v - line), i
    if worst <= epsilon:
        return [points[0], points[-1]]
    return _simplify(points[:at + 1], epsilon)[:-1] + _simplify(points[at:], epsilon)


def clip_from_frames(frames, fps, keep_blinks=False):
    """A clip's tracks from per-frame controls ([{control: value or missing}], `fps` frames a second)."""
    if not frames:
        raise ValueError("the video has no frames")
    dt = 1.0 / fps
    count = len(frames)
    duration = (count - 1) * dt
    if duration < 2 * EASE:
        raise ValueError("the gesture is too short: film at least a second")
    names = sorted({k for f in frames for k in f})
    neutral_frames = max(1, int(NEUTRAL * fps))
    tracks = {}
    for name in names:
        if name == "blink" and not keep_blinks:
            continue
        series = _fill([f.get(name) for f in frames])
        if series is None:
            continue
        smooth = OneEuro()
        series = [smooth(v, dt) for v in series]
        rest = 0.0 if name in ONE_SIDED else sorted(series[:neutral_frames])[neutral_frames // 2]
        values = []
        for i, v in enumerate(series):
            t = i * dt
            ease = min(1.0, t / EASE, (duration - t) / EASE)
            ease = ease * ease * (3 - 2 * ease)
            v = (v - rest) * ease
            values.append(_clamp(v, 0.0, 1.0) if name in ONE_SIDED else _clamp(v))
        if max(abs(v) for v in values) < QUIET:
            continue
        keys = _simplify([(round(i * dt, 3), v) for i, v in enumerate(values)], SIMPLIFY)
        keys = [[round(t, 3), round(v, 3)] for t, v in keys]
        keys[0][1] = keys[-1][1] = 0.0
        tracks[name] = keys
    if not tracks:
        raise ValueError("nothing moved enough to make a gesture")
    return tracks


def mirror_tracks(tracks):
    """The same clip with left and right swapped: turns and tilts the other way, the arms swapped."""
    out = {}
    for name, keys in tracks.items():
        if name in MIRROR_SIGN:
            keys = [[t, -v] for t, v in keys]
        out[MIRROR_SWAP.get(name) or {v: k for k, v in MIRROR_SWAP.items()}.get(name) or name] = keys
    return out


def entry_for(tracks, kind="gesture", words=None):
    """A library entry: it needs the control it moves most."""
    most = max(tracks, key=lambda k: max(abs(v) for _, v in tracks[k]))
    entry = {"kind": kind, "needs": [most], "tracks": tracks}
    if words:
        entry["words"] = words
    return entry
