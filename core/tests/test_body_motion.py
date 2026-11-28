"""Whole-body recording for 3D avatars (body_motion.py): bodies posed on the reference skeleton, turned into
what a phone's MediaPipe sends, solved back, with parts of the body hidden to try each failsafe."""
import json
import math
import os
import struct
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from companion import animations, body_motion as bm  # noqa: E402

I3 = np.eye(3)


def turn(axis, degrees):
    return bm._axis_angle(np.asarray(axis, float), degrees)


def pose(t=0.0, wave=0.0, squat=0.0, head=0.0, twist=0.0, lean=0.0):
    """A pose on the reference skeleton (local turns): the person's left arm raised forward and its elbow
    bent (wave), knees bent (squat, 0-1), the head turned (degrees), the waist twisted and the body leant."""
    local = {}
    if wave:
        local["leftUpperArm"] = turn((0, 0, 1), -60.0 * (1 - wave)) @ turn((0, 1, 0), -50.0 * wave) \
            @ turn((0, 0, 1), 40.0 * wave)
        local["leftLowerArm"] = turn((0, -1, 0), 70.0 * wave)
    else:
        local["leftUpperArm"] = turn((0, 0, 1), -70.0)
    local["rightUpperArm"] = turn((0, 0, 1), 70.0)
    local["rightLowerArm"] = turn((0, 1, 0), 15.0)
    if squat:
        knee = 80.0 * squat
        local["leftUpperLeg"] = turn((1, 0, 0), -knee / 2)
        local["rightUpperLeg"] = turn((1, 0, 0), -knee / 2)
        local["leftLowerLeg"] = turn((1, 0, 0), knee)
        local["rightLowerLeg"] = turn((1, 0, 0), knee)
        local["leftFoot"] = turn((1, 0, 0), -knee / 2)
        local["rightFoot"] = turn((1, 0, 0), -knee / 2)
    if head:
        local["neck"] = turn((0, 1, 0), 0.4 * head)
        local["head"] = turn((0, 1, 0), 0.6 * head)
    if twist:
        for b in ("spine", "chest", "upperChest"):
            local[b] = turn((0, 1, 0), twist / 3)
    if lean:
        local["hips"] = turn((1, 0, 0), lean)
    return local


def payload(poses, fps=15.0, hidden=None, mirror_world=False):
    """What a phone would send for these poses: MediaPipe's world points (metres from between the hips;
    x to the picture's right, y down, z away) with how sure it is of each, and where each is in the
    picture. hidden(k) -> names of points out of sight in frame k."""
    frames = []
    for k, local in enumerate(poses):
        p = bm.marks(local)
        mid = (p[bm.POINTS["left_hip"]] + p[bm.POINTS["right_hip"]]) / 2
        out = set(hidden(k)) if hidden else set()
        rows = []
        for name, i in sorted(bm.POINTS.items(), key=lambda kv: kv[1]):
            x, y, z = p[i] - mid
            ix, iy = 0.5 + 0.35 * p[i][0], 0.95 - 0.5 * p[i][1]
            v = 0.95
            if name in out:
                v, iy = 0.1, 1.2                    # below the picture, and MediaPipe unsure of it
            rows.append([round(float(x), 4), round(float(-y), 4), round(float(-z), 4), v, round(ix, 4), round(iy, 4)])
        frames.append({"face": None, "blend": None, "pose": None, "body": rows})
    return {"fps": fps, "width": 720, "height": 1280, "frames": frames}


def channels(data):
    """The bones a .vrma file turns, and whether it moves the hips."""
    total = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20:20 + total])
    names = [n["name"] for n in doc["nodes"]]
    turned = {names[c["target"]["node"]] for c in doc["animations"][0]["channels"] if c["target"]["path"] == "rotation"}
    moved = any(c["target"]["path"] == "translation" for c in doc["animations"][0]["channels"])
    return turned, moved


LEGS = ("left_knee", "right_knee", "left_ankle", "right_ankle", "left_heel", "right_heel", "left_foot_index",
        "right_foot_index")


class SolveTest(unittest.TestCase):
    def assert_matches(self, local, tolerance=0.02):
        """Solving the points of a pose gives back a pose with the same points (within 2 cm)."""
        p = bm.marks(local)
        solved = bm.solve(p, set(bm.PARTS), {})
        q = bm.marks(solved)
        for name in ("left_elbow", "left_wrist", "right_elbow", "right_wrist", "left_knee", "left_ankle",
                     "right_ankle", "nose", "left_ear", "left_shoulder"):
            i = bm.POINTS[name]
            self.assertLess(float(np.linalg.norm(p[i] - q[i])), tolerance, name)
        return solved

    def test_rest_solves_to_no_turns(self):
        local = bm.solve(bm.marks({}), set(bm.PARTS), {})
        self.assertLess(max(bm.angle(m) for m in local.values()), 1e-6)

    def test_poses_come_back(self):
        self.assert_matches(pose(wave=1.0))
        self.assert_matches(pose(head=35.0, twist=20.0))
        self.assert_matches(pose(squat=0.8))
        self.assert_matches(pose(lean=12.0, wave=0.5))

    def test_the_elbow_bends_as_far_as_it_was_bent(self):
        solved = self.assert_matches(pose(wave=1.0))
        self.assertAlmostEqual(bm.angle(solved["leftLowerArm"]), 70.0, delta=1.0)

    def test_elbows_and_knees_never_bend_backwards(self):
        local = pose()
        local["leftLowerArm"] = turn((0, -1, 0), -40.0)         # bent the impossible way
        local["leftLowerLeg"] = turn((1, 0, 0), -30.0)
        hinges = {"leftArm": np.array([0.0, -1.0, 0.0]), "leftLeg": np.array([1.0, 0.0, 0.0])}
        solved = bm.solve(bm.marks(local), set(bm.PARTS), hinges)
        self.assertLess(bm.angle(solved["leftLowerArm"]), 1e-6)
        self.assertLess(bm.angle(solved["leftLowerLeg"]), 1e-6)

    def test_a_straight_arm_keeps_its_twist_through_noise(self):
        rng = np.random.default_rng(3)
        hinges = {}
        twists = []
        for _ in range(40):
            p = bm.marks(pose())
            p = p + rng.normal(0.0, 0.004, p.shape)
            solved = bm.solve(p, set(bm.PARTS), hinges)
            twists.append(solved["rightUpperArm"])
        worst = max(bm.angle(a.T @ b) for a, b in zip(twists, twists[1:]))
        self.assertLess(worst, 15.0, "the arm flips round as noise moves the hinge")


class RecordingTest(unittest.TestCase):
    def make(self, poses, **options):
        mirror = options.pop("mirror", False)
        data, report = bm.make(payload(poses, **options), mirror=mirror)
        facts = animations.check(data)               # a VRM Animation file the device accepts
        return data, report, facts

    def wave(self, frames=45):
        return [pose(wave=0.5 + 0.5 * math.sin(k / 3.0)) for k in range(frames)]

    def test_the_whole_body(self):
        poses = [pose(squat=0.5 - 0.5 * math.cos(k / 5.0), wave=0.5) for k in range(45)]
        data, report, facts = self.make(poses)
        self.assertEqual(report["scope"], "whole body")
        self.assertEqual(report["left_out"], [])
        turned, moved = channels(data)
        self.assertTrue({"hips", "leftUpperLeg", "rightLowerLeg", "leftUpperArm", "head", "spine"} <= turned)
        self.assertTrue(moved, "the hips go down as the knees bend")
        self.assertAlmostEqual(facts["seconds"], 44 / 15.0, delta=0.05)

    def test_legs_out_of_the_picture_make_an_upper_body_clip(self):
        data, report, _ = self.make(self.wave(), hidden=lambda k: LEGS + ("left_hip", "right_hip"))
        self.assertEqual(report["scope"], "upper body")
        said = {o["part"]: o["why"] for o in report["left_out"]}
        self.assertEqual(said["left leg"], "not in the picture")
        self.assertIn("waist", said)
        turned, moved = channels(data)
        self.assertFalse({"hips", "leftUpperLeg", "rightUpperLeg", "leftLowerLeg"} & turned)
        self.assertFalse(moved)
        self.assertIn("leftUpperArm", turned)
        self.assertIn("Left to the avatar's own movement", bm.describe(report))

    def test_a_short_gap_is_bridged(self):
        hide = ("left_elbow", "left_wrist")
        _, report, _ = self.make(self.wave(), hidden=lambda k: hide if 20 <= k < 24 else ())
        self.assertIn("left arm", report["kept"])
        self.assertEqual([b["part"] for b in report["bridged"]], ["left arm", "left hand"])   # its wrist hid too

    def test_a_long_gap_rests_the_arm_and_brings_it_back(self):
        hide = ("left_elbow", "left_wrist")
        poses = [pose(wave=1.0)] * 75
        data, report, _ = self.make(poses, hidden=lambda k: hide if 20 <= k < 50 else ())
        self.assertEqual([b["part"] for b in report["eased"]], ["left arm", "left hand"])
        self.assertIn("left arm", report["kept"])

    def test_an_arm_seen_too_little_is_left_out(self):
        hide = ("right_elbow", "right_wrist")
        data, report, _ = self.make(self.wave(), hidden=lambda k: hide if k % 10 < 7 else ())
        said = {o["part"]: o["why"] for o in report["left_out"]}
        self.assertTrue(said["right arm"].startswith("seen only"))
        turned, _ = channels(data)
        self.assertNotIn("rightUpperArm", turned)
        self.assertIn("leftUpperArm", turned)

    def test_too_little_of_the_body_is_refused(self):
        hide = ("left_shoulder", "right_shoulder")
        with self.assertRaises(bm.BodyError) as caught:
            self.make(self.wave(), hidden=lambda k: hide if k > 10 else ())
        self.assertIn("further away", str(caught.exception))

    def test_frames_without_the_person_at_either_end_are_cut(self):
        nobody = ("left_shoulder", "right_shoulder", "left_elbow", "right_elbow")
        _, report, facts = self.make(self.wave(60), hidden=lambda k: nobody if k < 15 else ())
        self.assertAlmostEqual(report["trimmed"], 1.0, delta=0.1)
        self.assertAlmostEqual(facts["seconds"], 44 / 15.0, delta=0.1)

    def test_mirrored_the_other_arm_moves(self):
        poses = [pose(wave=1.0 if k > 10 else 0.0) for k in range(30)]
        plain, _, _ = self.make(poses)
        mirrored, _, _ = self.make(poses, mirror=True)

        def arm_travel(data, bone):
            doc_len = struct.unpack_from("<I", data, 12)[0]
            doc = json.loads(data[20:20 + doc_len])
            blob = data[20 + doc_len + 8:]
            names = [n["name"] for n in doc["nodes"]]
            for c in doc["animations"][0]["channels"]:
                if names[c["target"]["node"]] == bone and c["target"]["path"] == "rotation":
                    acc = doc["accessors"][doc["animations"][0]["samplers"][c["sampler"]]["output"]]
                    view = doc["bufferViews"][acc["bufferView"]]
                    q = np.frombuffer(blob, "<f4", acc["count"] * 4, view["byteOffset"]).reshape(-1, 4)
                    return math.degrees(2 * math.acos(min(1.0, abs(float(np.dot(q[0], q[-1]))))))
            return 0.0
        self.assertGreater(arm_travel(plain, "leftLowerArm"), 40.0)
        self.assertLess(arm_travel(plain, "rightLowerArm"), 5.0)
        self.assertGreater(arm_travel(mirrored, "rightLowerArm"), 40.0)
        self.assertLess(arm_travel(mirrored, "leftLowerArm"), 5.0)

    def test_bad_points_are_refused(self):
        body = payload(self.wave(10))
        body["frames"][3]["body"][5][0] = float("nan")
        with self.assertRaises(bm.BodyError):
            bm.make(body)
        body = payload(self.wave(10))
        body["frames"][3]["body"] = body["frames"][3]["body"][:20]
        with self.assertRaises(bm.BodyError):
            bm.make(body)
        with self.assertRaises(bm.BodyError):
            bm.make({"fps": 15, "frames": [{"body": None}] * 10})


if __name__ == "__main__":
    unittest.main()
