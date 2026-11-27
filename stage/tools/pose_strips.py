#!/usr/bin/env python3
"""Head close-ups in strips of poses, drawn by the stage, with the rest outline over each.

    pose_strips.py STAGE OUT.png PUPPET.inp [PUPPET2.inp ...] [--rows turn,nod,diagonal,body,tilt]
                   [--view head|body]

For each puppet and each row, renders the pose at -30, -15, 0, 15 and 30 (body
turn and lean, and arms, at -10 to 10) with every other setting held at rest,
so only that movement shows. The arm rows (arm_a: both arms' A setting,
arm_b: both B settings) are best seen with --view body, the whole character. The outline of the character at rest is drawn in cyan on
every frame: what moves off it, and what is left behind, is easy to see.
Several puppets give rows one above the other, for before and after.

Needs Xvfb and Pillow, and studio/live2d_export.py beside this repository's stage.
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "studio"))
import live2d_export  # noqa: E402

SIZE = int(os.environ.get("STRIP_SIZE", "360"))
HOLD = 1.5
REST = {"ParamAngleX": 0, "ParamAngleY": 0, "ParamAngleZ": 0, "ParamBodyAngleX": 0, "ParamBodyAngleY": 0,
        "ParamBodyAngleZ": 0, "ParamBreath": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ParamMouthOpenY": 0,
        "ParamMouthForm": 0, "ParamArmLA": 0, "ParamArmRA": 0, "ParamArmLB": 0, "ParamArmRB": 0}
STEPS = (-1.0, -0.5, 0.0, 0.5, 1.0)
ROWS = {
    "turn": lambda s: {"ParamAngleX": 30 * s},
    "nod": lambda s: {"ParamAngleY": 30 * s},
    "diagonal": lambda s: {"ParamAngleX": 30 * s, "ParamAngleY": 30 * s},
    "tilt": lambda s: {"ParamAngleZ": 30 * s},
    "body": lambda s: {"ParamBodyAngleX": 10 * s},
    "lean": lambda s: {"ParamBodyAngleY": 10 * s},
    "arm_a": lambda s: {"ParamArmLA": 10 * s, "ParamArmRA": 10 * s},
    "arm_b": lambda s: {"ParamArmLB": 10 * s, "ParamArmRB": 10 * s},
}


def head_view(path):
    """The puppet area around the head (and neck), square, in puppet coordinates."""
    with open(path, "rb") as f:
        puppet = live2d_export.Puppet(f.read())
    head = [p for p in puppet.parts if any(a.name == "head" for a in p.ancestors()) or p.name.endswith("neck")]
    pts = np.vstack([puppet.world(p, puppet.defaults())[p.verts[:, 1] <= np.percentile(p.verts[:, 1], 100)] for p in head])
    face = [p for p in puppet.parts if p.name.endswith("face_base")]
    fx = np.vstack([puppet.world(p, puppet.defaults()) for p in face]) if face else pts
    cx = (fx[:, 0].min() + fx[:, 0].max()) / 2
    top = pts[:, 1].min()
    side = 2.4 * (fx[:, 0].max() - fx[:, 0].min())
    return (cx - side / 2, top - 0.08 * side, cx + side / 2, top + 0.92 * side)


def body_view(path):
    """The whole character at rest, square, with a margin, in puppet coordinates."""
    with open(path, "rb") as f:
        puppet = live2d_export.Puppet(f.read())
    pts = np.vstack([puppet.world(p, puppet.defaults()) for p in puppet.parts])
    cx, cy = (pts[:, 0].min() + pts[:, 0].max()) / 2, (pts[:, 1].min() + pts[:, 1].max()) / 2
    side = 1.25 * max(pts[:, 0].max() - pts[:, 0].min(), pts[:, 1].max() - pts[:, 1].min())
    return (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)


def render(stage, puppet, rows, whole=False):
    view = body_view(puppet) if whole else head_view(puppet)
    lines, captures, t = [], [], 0.5
    poses = [("rest", {})] + [(row, ROWS[row](s)) for row in rows for s in STEPS]
    for _, values in poses:
        lines.append({"at": round(t, 2), "set": dict(REST, **values), "ease": 0})
        t += HOLD
        captures.append(round(t - 0.05, 2))
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "script.jsonl")
        with open(script, "w") as f:
            f.write("\n".join(json.dumps(line) for line in lines) + "\n")
        frames = os.path.join(tmp, "frames")
        os.makedirs(frames)
        cmd = ["xvfb-run", "-a", "-s", "-screen 0 %dx%dx24" % (SIZE + 64, SIZE + 64), stage, puppet,
               "--size", "%dx%d" % (SIZE, SIZE), "--gl", "auto", "--still", "--no-stdin",
               "--view", ",".join("%.2f" % v for v in view), "--script", script, "--fixed-dt", "0.02",
               "--capture-dir", frames, "--capture-at", ",".join("%.2f" % c for c in captures),
               "--exit-after", "%.2f" % (captures[-1] + 0.1)]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if run.returncode != 0:
            raise RuntimeError("stage failed: %s" % run.stderr[-400:])
        images = [Image.open(os.path.join(frames, "frame_%03d.png" % i)).convert("RGB") for i in range(len(poses))]
    rest = np.asarray(images[0]).astype(int)
    background = rest[1, 1]
    covered = np.abs(rest - background).sum(-1) > 30
    edge = covered & ~(np.roll(covered, 1, 0) & np.roll(covered, -1, 0) & np.roll(covered, 1, 1) & np.roll(covered, -1, 1))
    out = {}
    for row in rows:
        start = 1 + rows.index(row) * len(STEPS)
        tiles = []
        for image in images[start:start + len(STEPS)]:
            a = np.asarray(image).copy()
            a[edge] = (0, 220, 255)
            tiles.append(Image.fromarray(a))
        out[row] = tiles
    return out


def main(argv):
    rows = ["turn", "nod", "diagonal", "body"]
    if "--rows" in argv:
        i = argv.index("--rows")
        rows = argv[i + 1].split(",")
        argv = argv[:i] + argv[i + 2:]
    whole = False
    if "--view" in argv:
        i = argv.index("--view")
        whole = argv[i + 1] == "body"
        argv = argv[:i] + argv[i + 2:]
    stage, out_path, puppets = argv[1], argv[2], argv[3:]
    results = [(os.path.basename(os.path.dirname(os.path.abspath(p))) or p, render(stage, p, rows, whole))
               for p in puppets]
    label_w, gap = 150, 4
    sheet = Image.new("RGB", (label_w + len(STEPS) * (SIZE + gap), 24 + len(rows) * len(results) * (SIZE + gap)),
                      (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    for j, s in enumerate(STEPS):
        draw.text((label_w + j * (SIZE + gap) + SIZE // 2 - 10, 6), "%+d%%" % int(100 * s), fill=(0, 0, 0))
    y = 24
    for row in rows:
        for name, frames in results:
            draw.text((6, y + SIZE // 2), "%s\n%s" % (row, name), fill=(0, 0, 0))
            for j, tile in enumerate(frames[row]):
                sheet.paste(tile, (label_w + j * (SIZE + gap), y))
            y += SIZE + gap
    sheet.save(out_path)
    print("wrote", out_path, sheet.size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
