#!/usr/bin/env python3
"""Visual test for the stage.

Runs the stage on a virtual display with a rigged puppet, holds it still, and
checks that commands move the right part of the picture:

  * the character is drawn at all,
  * opening the mouth changes a small area,
  * blinking changes a different small area,
  * after both, the picture returns to where it started,
  * live movement (a person copied, live.rs) turns the head, and the character takes it back after.

Usage: visual_test.py STAGE_BINARY PUPPET.inp OUT_DIR [--gl es|desktop|auto]

Needs Xvfb and Pillow. Exit status 0 means every check passed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

SIZE = (600, 800)
BACKGROUND = (16, 16, 20)


def changed_box(a, b, threshold=24):
    """Bounding box and pixel count of where two frames differ."""
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    mask = diff.point(lambda v: 255 if v > threshold else 0)
    return mask.getbbox(), mask.histogram()[255]


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    binary, puppet, out_dir = sys.argv[1:4]
    gl = sys.argv[sys.argv.index("--gl") + 1] if "--gl" in sys.argv else "auto"
    os.makedirs(out_dir, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith("frame_"):
            os.remove(os.path.join(out_dir, name))
    if not shutil.which("Xvfb"):
        print("SKIP: Xvfb is not installed")
        return 0

    # Let Xvfb pick a free display number and tell us which one it took.
    xvfb = subprocess.Popen(["Xvfb", "-displayfd", "1", "-screen", "0", "800x1000x24"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    display = ":" + xvfb.stdout.readline().strip()
    if display == ":":
        print("FAIL  could not start a virtual display")
        return 1
    try:
        script = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        script.write("\n".join([
            # The first ten seconds let the puppet's own hair and cloth physics settle.
            '{"at": 0.5, "state": "speaking", "mouth": 0}',
            '{"at": 10.2, "mouth": 1}',
            '{"at": 11.2, "mouth": 0}',
            '{"at": 12.0, "blink": true}',
            '{"at": 13.2, "live": {"head_yaw": 0.9, "head_roll": 0.5}}',
            '{"at": 13.4, "live": {"head_yaw": 0.9, "head_roll": 0.5}}',
            '{"at": 13.7, "live": null}',
        ]) + "\n")
        script.close()
        env = dict(os.environ, DISPLAY=display, LIBGL_ALWAYS_SOFTWARE="1")
        env.pop("WAYLAND_DISPLAY", None)
        cmd = [binary, puppet, "--size", "%dx%d" % SIZE, "--gl", gl, "--still", "--no-stdin",
               "--script", script.name, "--fixed-dt", "0.02",
               "--capture-dir", out_dir, "--capture-at", "10.0,11.0,12.09,13.0,13.6,14.6", "--exit-after", "14.7"]
        run = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
        os.unlink(script.name)
    finally:
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
    check(ready is not None, "stage reported ready: %s" % (ready and {k: ready[k] for k in ("opengl", "parts", "params")}))
    if ready:
        mapped = {c["control"] for c in ready["controls"]}
        check({"mouth_open", "blink_left", "blink_right"} <= mapped, "mouth and blink controls were mapped: %s" % sorted(mapped))
    check(not any(e.get("event") in ("error", "fatal") for e in events), "no errors reported")
    gl_errors = [l for l in run.stderr.splitlines() if "GL_INVALID" in l or "incomplete" in l.lower()]
    check(not gl_errors, "no OpenGL errors (%d lines)" % len(gl_errors))

    paths = [os.path.join(out_dir, "frame_%03d.png" % i) for i in range(6)]
    if not all(os.path.exists(p) for p in paths):
        check(False, "all six frames were captured")
        print(run.stderr[-2000:])
        return 1
    rest, mouth, blink, after, live, given_back = [Image.open(p) for p in paths]
    area = SIZE[0] * SIZE[1]

    _, drawn = changed_box(rest, Image.new("RGB", rest.size, BACKGROUND))
    check(drawn > 0.05 * area, "character is drawn (%.0f%% of the frame)" % (100.0 * drawn / area))
    width, height = rest.size
    check((width, height) == SIZE, "frame size is %dx%d" % SIZE)
    box, _ = changed_box(rest, Image.new("RGB", rest.size, BACKGROUND))
    check(box is not None and box[0] > 0 and box[1] > 0 and box[2] < width and box[3] < height,
          "whole character fits inside the frame: %s" % (box,))

    mbox, mcount = changed_box(rest, mouth)
    check(mbox is not None and mcount > 30, "opening the mouth changes the picture (%d pixels)" % mcount)
    bbox, bcount = changed_box(rest, blink)
    check(bbox is not None and bcount > 30, "blinking changes the picture (%d pixels)" % bcount)
    if mbox and bbox:
        marea = (mbox[2] - mbox[0]) * (mbox[3] - mbox[1])
        barea = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        check(marea < 0.02 * area, "mouth change is confined to a small area %s" % (mbox,))
        check(barea < 0.04 * area, "blink change is confined to a small area %s" % (bbox,))
        mouth_y = (mbox[1] + mbox[3]) / 2
        blink_y = (bbox[1] + bbox[3]) / 2
        check(blink_y < mouth_y, "eyes are above the mouth (y %.0f < %.0f)" % (blink_y, mouth_y))
    _, back = changed_box(rest, after)
    check(back < 30, "picture returns to rest afterwards (%d pixels differ)" % back)

    lbox, lcount = changed_box(rest, live)
    check(lbox is not None and lcount > 300, "live movement turns the head (%d pixels)" % lcount)
    if lbox and bbox:
        check(lbox[1] < bbox[3], "and it is the head that moves %s" % (lbox,))
    _, back = changed_box(rest, given_back)
    check(back < 30, "the character takes its head back after live movement (%d pixels differ)" % back)

    print("%d check(s) failed" % len(failures) if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
