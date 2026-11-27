"""The character copies a person live ("Copy me", docs/26-animation-director.md, "Live from a phone").

Two kinds of source give it frames, one at a time:
- the live camera in a phone's browser (the recorder page over the device's own HTTPS, record.js): the
  same MediaPipe points a phone sends when it records a gesture, made into controls by mocap.py;
- a face-tracking app on the phone (VTube Studio or MeowFace, iFacialMocap: phone_apps.py).

Each frame's controls are measured from the person's resting pose (their first second, or again when
they recentre), turned like a reflection if the owner wants (the default: it copies you as a mirror
does), smoothed, and sent to the stage at most 30 times a second as {"live": {...}} (stage/src/live.rs).
The stage hands the controls back to the character when updates stop.

What is shown can also be recorded, up to 20 seconds, into a gesture: resampled to a steady rate and
made into a clip exactly as a recorded video is (mocap.clip_from_frames).
"""
import collections
import statistics
import time

from . import mocap, phone_record

RATE = 30.0                 # updates a second to the stage, at most
CALIBRATE = 1.0             # seconds of frames that give the resting pose
RECORD_FPS = 15.0
LOST = 2.0                  # seconds without a frame: the source has gone quiet


def _clamp(name, value):
    return max(0.0, min(1.0, value)) if name in mocap.ONE_SIDED else max(-1.2, min(1.2, value))


def mirrored(controls):
    """The same controls with left and right swapped, as a reflection moves."""
    out = {}
    swap = dict(mocap.MIRROR_SWAP, **{v: k for k, v in mocap.MIRROR_SWAP.items()})
    for name, value in controls.items():
        out[swap.get(name, name)] = -value if name in mocap.MIRROR_SIGN else value
    return out


def flipped(controls):
    """Up and down the other way (for an app whose nod comes the other way)."""
    return {name: -value if name in ("head_pitch", "gaze_y") else value for name, value in controls.items()}


class LiveMotion:
    def __init__(self, stage, clock=time.monotonic, speaking=lambda: False, changed=lambda: None):
        self.stage = stage
        self.clock = clock
        self.speaking = speaking        # while the character talks, its mouth is its own
        self.changed = changed          # told when the status changes (for the settings page)
        self.source = None
        self.mirror = True
        self.flip = False
        self._fresh()

    def _fresh(self):
        self.rest = None
        self.calibrating = []
        self.filters = {}
        self.last_sent = 0.0
        self.times = collections.deque()
        self.face = False
        self.recording = None
        self.recorded = None
        self.record_error = ""

    # ----- the source
    def begin(self, kind, label="", mirror=None, flip=False):
        """A source starts (a new one takes over from any other)."""
        if self.source:
            self.stage.send({"live": None})
        self.source = {"kind": kind, "label": label, "since": self.clock()}
        if mirror is not None:
            self.mirror = bool(mirror)
        self.flip = bool(flip)
        self._fresh()
        self.changed()

    def end(self, kind=None):
        """The source stops (only if it is `kind`, when given); the character has its controls back."""
        if not self.source or (kind and self.source["kind"] != kind):
            return
        self.source = None
        self.recording = None
        self.stage.send({"live": None})
        self.changed()

    def recentre(self):
        """Take the resting pose again (the person sits differently now)."""
        self.rest = None
        self.calibrating = []
        self.changed()

    def set_mirror(self, on):
        self.mirror = bool(on)

    def set_flip(self, on):
        self.flip = bool(on)

    # ----- frames
    def frame(self, controls, kind):
        """One frame's controls (mocap's, not yet measured from rest) from the source `kind`. Returns
        whether it was used."""
        if not self.source or self.source["kind"] != kind:
            return False
        now = self.clock()
        self.times.append(now)
        while self.times and self.times[0] < now - 1.0:
            self.times.popleft()
        found = bool(controls)
        if found != self.face:
            self.face = found
            self.changed()
        if not found:
            return True                  # nobody in view: the stage holds still, then hands back
        if self.rest is None:
            self.calibrating.append((now, controls))
            if now - self.calibrating[0][0] < CALIBRATE:
                return True
            names = {n for _, c in self.calibrating for n in c}
            self.rest = {n: statistics.median(c[n] for _, c in self.calibrating if n in c) for n in names}
            self.calibrating = []
            self.changed()
        values = {}
        for name, value in controls.items():
            rest = 0.0 if name in mocap.ONE_SIDED else self.rest.get(name, 0.0)
            values[name] = value - rest
        if self.mirror:
            values = mirrored(values)
        if self.flip:
            values = flipped(values)
        if self.recording is not None:
            self.recording.append((now, dict(values)))
            if now - self.recording[0][0] >= phone_record.MAX_SECONDS:
                try:                             # the longest a gesture can be: kept for whoever asks for it
                    self.recorded = self.stop_recording()
                except ValueError as error:
                    self.record_error = str(error)
                self.changed()
        shown = {}
        for name, value in values.items():
            smooth = self.filters.get(name)
            if smooth is None:
                smooth = self.filters[name] = [mocap.OneEuro(min_cutoff=1.5, beta=0.5), None]
            dt = now - smooth[1] if smooth[1] is not None else 1.0 / RATE
            smooth[1] = now
            shown[name] = _clamp(name, smooth[0](value, max(dt, 1e-3)))
        if self.speaking():
            shown.pop("mouth_open", None)
        if now - self.last_sent >= 1.0 / RATE:
            self.last_sent = now
            self.stage.send({"live": {k: round(v, 3) for k, v in sorted(shown.items())}})
        return True

    # ----- recording what is shown
    def start_recording(self):
        if not self.source:
            raise ValueError("start copying first")
        if self.rest is None:
            raise ValueError("hold still a moment first, facing the camera")
        self.recording = []
        self.recorded = None
        self.record_error = ""
        self.changed()

    def stop_recording(self):
        """End the recording and return the clip's tracks (or a ValueError says what is wrong). A recording
        that stopped by itself at the longest a gesture can be is returned once, when asked for."""
        frames, self.recording = self.recording, None
        if frames is None:
            tracks, self.recorded = self.recorded, None
            error, self.record_error = self.record_error, ""
            if tracks:
                return tracks
            raise ValueError(error or "nothing is being recorded")
        self.changed()
        if len(frames) < 2 or frames[-1][0] - frames[0][0] < 1.0:
            raise ValueError("the gesture is too short: record at least a second")
        steady = resample(frames, RECORD_FPS)
        return phone_record.fit_keys(mocap.clip_from_frames(steady, RECORD_FPS))

    # ----- for the settings page
    def status(self):
        now = self.clock()
        if not self.source:
            return {"on": False, "mirror": self.mirror, "flip": self.flip}
        quiet = not self.times or now - self.times[-1] > LOST
        recording = None
        if self.recording is not None:
            recording = round(now - self.recording[0][0], 1) if self.recording else 0.0
        return {"on": True, "kind": self.source["kind"], "label": self.source["label"], "mirror": self.mirror,
                "flip": self.flip, "fps": 0 if quiet else len(self.times), "face": self.face and not quiet,
                "calibrating": self.rest is None, "recording": recording,
                "recorded": self.recorded is not None or bool(self.record_error)}


def resample(frames, fps):
    """Frames [(time, {control: value})] at a steady rate: each step takes the controls of the frame
    nearest it (frames come unevenly over a network)."""
    start, end = frames[0][0], frames[-1][0]
    count = int((end - start) * fps) + 1
    out, j = [], 0
    for i in range(count):
        t = start + i / fps
        while j + 1 < len(frames) and abs(frames[j + 1][0] - t) <= abs(frames[j][0] - t):
            j += 1
        out.append(dict(frames[j][1]))
    return out
