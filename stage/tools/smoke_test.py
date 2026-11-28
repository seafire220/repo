#!/usr/bin/env python3
"""Smoke test for the stage on any computer: it starts, reports its OpenGL, draws a picture and stops.

    smoke_test.py STAGE_BINARY OUT_DIR [--gl es|desktop|auto]

Draws the device's own setup card (a one-part puppet the core makes, core/companion/screen.py), so no
character is needed. On Linux it uses a virtual display (Xvfb) when there is one; on a Mac or Windows,
the computer's own display (a window opens for a second or two). Exit status 0 means every check passed.
Needs Pillow.
"""
import json
import os
import shutil
import subprocess
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "core"))
from companion import screen  # noqa: E402

SIZE = (600, 800)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    binary, out_dir = sys.argv[1:3]
    gl = sys.argv[sys.argv.index("--gl") + 1] if "--gl" in sys.argv else "auto"
    os.makedirs(out_dir, exist_ok=True)
    puppet = os.path.join(out_dir, "card.inp")
    with open(puppet, "wb") as f:
        f.write(screen.card_puppet(screen.card_png("Hello!", ["A smoke test of the stage on this computer."])))
    frame = os.path.join(out_dir, "frame_000.png")
    if os.path.exists(frame):
        os.remove(frame)

    env = dict(os.environ)
    xvfb = None
    if sys.platform.startswith("linux"):
        if not shutil.which("Xvfb"):
            print("SKIP: Xvfb is not installed")
            return 0
        xvfb = subprocess.Popen(["Xvfb", "-displayfd", "1", "-screen", "0", "800x1000x24"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        env["DISPLAY"] = ":" + xvfb.stdout.readline().strip()
        env.pop("WAYLAND_DISPLAY", None)
    try:
        cmd = [binary, puppet, "--size", "%dx%d" % SIZE, "--gl", gl, "--still", "--no-stdin", "--fixed-dt", "0.02",
               "--capture-dir", out_dir, "--capture-at", "1.0", "--exit-after", "1.5"]
        run = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    finally:
        if xvfb is not None:
            xvfb.terminate()

    events = []
    for line in run.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    failures = []

    def check(ok, message):
        print(("PASS  " if ok else "FAIL  ") + message)
        if not ok:
            failures.append(message)

    ready = next((e for e in events if e.get("event") == "ready"), None)
    check(run.returncode == 0, "stage exited cleanly (status %s)" % run.returncode)
    check(ready is not None, "stage reported ready, with OpenGL %s" % (ready or {}).get("opengl"))
    check(not any(e.get("event") in ("error", "fatal") for e in events), "no errors reported")
    gl_errors = [l for l in run.stderr.splitlines() if "GL_INVALID" in l or "compile" in l.lower()]
    check(not gl_errors, "no OpenGL or shader errors (%d lines)" % len(gl_errors))
    if not os.path.exists(frame):
        check(False, "a frame was captured")
    else:
        with Image.open(frame) as im:
            colours = im.convert("RGB").getcolors(1 << 20) or []
        check(len(colours) > 20, "the card was drawn (%d colours in the frame)" % len(colours))
    if failures:
        print(run.stderr[-3000:])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
