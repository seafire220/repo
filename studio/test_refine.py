"""Tests for refine.py on a small hand-built puppet. No artwork is needed."""
import copy
import math
import unittest

import refine

UUID = iter(range(1, 1000))


def part(name, x0, y0, x1, y1, step=4):
    """A Part whose mesh is a grid of points over a box, positioned by its own offset."""
    verts = []
    y = y0
    while y <= y1:
        x = x0
        while x <= x1:
            verts += [x, y]
            x += step
        y += step
    return {"uuid": next(UUID), "name": name, "type": "Part", "transform": {"trans": [0.0, 0.0, 0.0]},
            "mesh": {"verts": verts}, "children": []}


def puppet():
    """A head (face, eyes, mouth, hair) on a neck and body. y grows downwards; the chin is at y=0."""
    head = {"uuid": next(UUID), "name": "head", "type": "Node", "transform": {"trans": [0.0, -100.0, 0.0]},
            "children": [
                part("24_face_base", -50, -100, 50, 0),
                part("30_eye_white_l", -35, -55, -15, -45, 2), part("40_pupil_l", -32, -56, -18, -44, 2),
                part("50_eye_l", -36, -58, -14, -44, 2), part("52_eye_closed_l", -36, -50, -14, -46, 2),
                part("30_eye_white_r", 15, -55, 35, -45, 2), part("40_pupil_r", 18, -56, 32, -44, 2),
                part("50_eye_r", 14, -58, 36, -44, 2),
                part("58_mouth_cavity", -8, -18, 8, -8, 2), part("60_mouth", -8, -19, 8, -17, 1),
                part("80_hair_front", -70, -150, 70, -60), part("20_hair_back", -60, -150, 60, -110),
            ]}
    body = {"uuid": next(UUID), "name": "body", "type": "Node", "transform": {"trans": [0.0, 0.0, 0.0]},
            "children": [part("10_torso", -80, -80, 80, 200), part("14_neck", -15, -110, 15, -60), head]}
    nodes = {"uuid": next(UUID), "name": "Root", "type": "Node", "transform": {"trans": [0.0, 0.0, 0.0]},
             "children": [body]}
    params = []
    for name in refine.ANGLE_PARAMS:
        params.append({"name": name, "axis_points": [[0.0, 0.5, 1.0], [0.0]], "bindings": [
            {"node": head["uuid"], "param_name": "transform.r.z", "values": [[0.1], [0.0], [-0.1]],
             "isSet": [[True]] * 3, "interpolate_mode": "Linear"}]})
    mouth = {"name": refine.MOUTH_PARAM, "axis_points": [[0.0, 1.0], [0.0]], "bindings": []}
    params.append(mouth)
    # As the rigger makes it: the body turn shifts every head part sideways by the same amount.
    shift = [{"node": n["uuid"], "param_name": "deform",
              "values": [[[[-40.0, 0.0]] * (len(n["mesh"]["verts"]) // 2)], [[[0.0, 0.0]] * (len(n["mesh"]["verts"]) // 2)],
                         [[[40.0, 0.0]] * (len(n["mesh"]["verts"]) // 2)]],
              "isSet": [[True]] * 3, "interpolate_mode": "Linear"} for n in head["children"]]
    params.append({"name": "ParamBodyAngleX", "axis_points": [[0.0, 0.5, 1.0], [0.0]], "bindings": shift})
    return {"nodes": nodes, "param": params}


def binding(p, param, role, kind="deform"):
    names = {}

    def walk(n):
        names[n["uuid"]] = n["name"]
        for c in n.get("children") or []:
            walk(c)
    walk(p["nodes"])
    prm = next(x for x in p["param"] if x["name"] == param)
    found = [b for b in prm["bindings"] if refine.role_of(names[b["node"]]) == role and b["param_name"] == kind]
    return found[0] if found else None


def binding_list(p, param):
    return next(x for x in p["param"] if x["name"] == param)["bindings"]


def mesh_points(p, role):
    for item in refine.walk_parts(p["nodes"]):
        if item["role"] == role:
            return refine.points(item)
    raise KeyError(role)


class RefineTest(unittest.TestCase):
    def setUp(self):
        self.original = puppet()
        self.p = copy.deepcopy(self.original)
        self.report = refine.refine(self.p)

    def offsets(self, param, role, position):
        b = binding(self.p, param, role)
        index = {-1: 0, 0: 1, 1: 2}[position]
        return b["values"][index][0]

    def test_the_rigid_head_rotation_is_replaced(self):
        for name in refine.ANGLE_PARAMS:
            prm = next(x for x in self.p["param"] if x["name"] == name)
            self.assertFalse(any(b["param_name"].startswith("transform.") for b in prm["bindings"]))
            self.assertIsNotNone(binding(self.p, name, "face_base"))

    def moved_width(self, role, position):
        pts = mesh_points(self.p, role)
        moves = self.offsets("ParamAngleX", role, position)
        xs = [x + dx for (x, _), (dx, _) in zip(pts, moves)]
        return max(xs) - min(xs)

    def test_a_turn_narrows_the_far_eye_and_widens_the_near_eye(self):
        rest = self.moved_width("eye_l", 0)
        # Turning right (+): the right side of the face turns away.
        self.assertLess(self.moved_width("eye_r", 1), rest)
        self.assertGreater(self.moved_width("eye_l", 1), rest)
        self.assertLess(self.moved_width("eye_l", -1), rest)
        self.assertGreater(self.moved_width("eye_r", -1), rest)
        self.assertGreater(self.moved_width("eye_r", 1), 0.6 * rest, "narrower, not squashed flat")

    def test_the_pieces_of_an_eye_move_together(self):
        # The eye and its closed eye share one grid, so where they overlap they move alike.
        shared = {}
        for role in ("eye_l", "eye_closed_l"):
            for pt, move in zip(mesh_points(self.p, role), self.offsets("ParamAngleX", role, 1)):
                shared.setdefault((round(pt[0], 3), round(pt[1], 3)), []).append(tuple(move))
        both = [moves for moves in shared.values() if len(moves) == 2]
        self.assertTrue(both)
        self.assertTrue(all(abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3 for a, b in both))

    def test_a_turn_moves_the_middle_of_the_face_most_and_holds_the_head_outline(self):
        pts = mesh_points(self.p, "face_base")
        moves = self.offsets("ParamAngleX", "face_base", 1)
        middle = max(m[0] for m in moves)
        near = [m[0] for m, (x, y) in zip(moves, pts) if x <= -49 and abs(y + 150) < 3]
        far = [m[0] for m, (x, y) in zip(moves, pts) if x >= 49 and abs(y + 150) < 3]
        self.assertGreater(middle, 0.1 * 100 * 0.9, "the middle moves about a tenth of the face width or more")
        self.assertTrue(all(f < n for f, n in zip(far, near)), "the side turning away moves least: it narrows")
        self.assertTrue(all(n < middle for n in near))
        hair_pts = mesh_points(self.p, "hair_front")
        hair_moves = self.offsets("ParamAngleX", "hair_front", 1)
        rim = [m[0] for m, (x, y) in zip(hair_moves, hair_pts) if abs(x) >= 69]
        self.assertTrue(all(0 < r < 0.4 * middle for r in rim),
                        "the outline moves slightly towards the turn: the head turns about the neck behind it")

    def test_hair_behind_the_head_moves_the_other_way(self):
        pts = mesh_points(self.p, "hair_back")
        moves = self.offsets("ParamAngleX", "hair_back", 1)
        middle = [dx for (x, _), (dx, _) in zip(pts, moves) if abs(x) < 10]
        face = max(dx for dx, _ in self.offsets("ParamAngleX", "face_base", 1))
        self.assertLess(min(middle), -1.0, "its middle, behind the neck's axis, moves the other way")
        self.assertTrue(all(dx < 0.4 * face for dx in middle), "and none of it moves as far as the face")

    def test_turning_the_other_way_mirrors_and_rest_is_untouched(self):
        def mean_dx(position):
            moves = self.offsets("ParamAngleX", "mouth", position)
            return sum(dx for dx, _ in moves) / len(moves)
        self.assertAlmostEqual(mean_dx(1), -mean_dx(-1), places=2)
        self.assertEqual(self.offsets("ParamAngleX", "face_base", 0)[0], [0.0, 0.0])

    def test_a_nod_moves_the_features_up_or_down(self):
        down = self.offsets("ParamAngleY", "eye_l", -1)[0]
        up = self.offsets("ParamAngleY", "eye_l", 1)[0]
        self.assertGreater(down[1], 0)
        self.assertLess(up[1], 0)

    def test_a_tilt_turns_the_whole_head_as_one_piece_and_bends_the_neck(self):
        pts = {role: mesh_points(self.p, role) for role in ("face_base", "eye_l", "hair_front", "neck")}
        moved = {}
        for role, plist in pts.items():
            moves = self.offsets("ParamAngleZ", role, 1)
            moved[role] = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(plist, moves)]
        a0, b0 = pts["eye_l"][0], pts["face_base"][-1]
        a1, b1 = moved["eye_l"][0], moved["face_base"][-1]
        self.assertAlmostEqual(math.dist(a0, b0), math.dist(a1, b1), places=1)
        self.assertGreater(abs(moved["eye_l"][0][0] - pts["eye_l"][0][0]), 3, "the head visibly leans")
        neck_moves = self.offsets("ParamAngleZ", "neck", 1)
        bottom = [abs(dx) + abs(dy) for (x, y), (dx, dy) in zip(pts["neck"], neck_moves) if y >= -61]
        self.assertTrue(all(b < 0.5 for b in bottom), "the bottom of the neck stays on the body")

    def test_a_body_turn_carries_the_head_and_neck_together_a_little(self):
        self.assertEqual(self.report["body"], ["ParamBodyAngleX"])
        head = self.offsets("ParamBodyAngleX", "face_base", 1)
        neck = self.offsets("ParamBodyAngleX", "neck", 1)
        shift = sum(dx for dx, _ in head) / len(head)
        self.assertTrue(all(abs(dx - shift) < 2.5 and abs(dy) < 1e-6 for dx, dy in head + neck),
                        "head and neck move together, by about the same few pixels")
        chin = max(y for _, y in mesh_points(self.p, "face_base"))
        at_chin = [dx for (x, y), (dx, _) in zip(mesh_points(self.p, "face_base"), head) if y == chin and abs(x) < 10]
        neck_top = min(y for _, y in mesh_points(self.p, "neck"))
        top = [dx for (x, y), (dx, _) in zip(mesh_points(self.p, "neck"), neck) if y == neck_top and abs(x) < 10]
        self.assertLess(abs(sum(at_chin) / len(at_chin) - sum(top) / len(top)), 0.5, "no gap where they meet")
        self.assertTrue(0 < shift < 0.1 * 80, "a few pixels, not the rigger's 40")
        torso = self.offsets("ParamBodyAngleX", "torso", 1)
        self.assertGreater(max(dx for dx, _ in torso), shift, "the chest moves more than the spine")
        self.assertEqual(self.offsets("ParamBodyAngleX", "face_base", 0)[0], [0.0, 0.0])
        again = copy.deepcopy(self.p)
        parts = refine.walk_parts(again["nodes"])
        refine.refine_body(again, parts, refine.head_geometry(parts))
        self.assertEqual(len(binding_list(again, "ParamBodyAngleX")), len(binding_list(self.p, "ParamBodyAngleX")),
                         "running it twice adds nothing")

    def test_the_mouth_line_fades_as_the_open_mouth_grows_from_it(self):
        prm = next(x for x in self.p["param"] if x["name"] == refine.MOUTH_PARAM)
        self.assertEqual(prm["axis_points"][0], [0.0, refine.MOUTH_SHOWN, 1.0])
        line = binding(self.p, refine.MOUTH_PARAM, "mouth", "opacity")["values"]
        self.assertEqual([v[0] for v in line], [1.0, refine.LINE_AT_SHOWN, 0.0])
        cavity = binding(self.p, refine.MOUTH_PARAM, "mouth_cavity", "opacity")["values"]
        self.assertEqual([v[0] for v in cavity], [0.0, 1.0, 1.0])
        shut = binding(self.p, refine.MOUTH_PARAM, "mouth_cavity")["values"][0][0]
        pts = mesh_points(self.p, "mouth_cavity")
        top = min(y for _, y in pts)
        self.assertTrue(all(abs(y + dy - top) < 1e-6 for (_, y), (_, dy) in zip(pts, shut)), "shut: flat on the line")

    def test_puppets_without_a_head_or_face_are_refused(self):
        p = puppet()
        p["nodes"]["children"][0]["children"][2]["name"] = "something"
        with self.assertRaises(ValueError):
            refine.refine(p)
        p = puppet()
        head = p["nodes"]["children"][0]["children"][2]
        head["children"] = [c for c in head["children"] if "face_base" not in c["name"]]
        with self.assertRaises(ValueError):
            refine.refine(p)

    def test_the_file_round_trips_with_its_textures(self):
        data = refine.write_puppet(self.original, b"TEX_SECT\x00\x00\x00\x00")
        back, rest = refine.read_puppet(data)
        self.assertEqual(rest, b"TEX_SECT\x00\x00\x00\x00")
        self.assertEqual(back["param"][0]["name"], "ParamAngleX")


def puppet_with_arm(bend=None):
    """The puppet above with a left arm as the rigger makes it: upper arm and forearm overlapping at the
    elbow, each turned by moved copies of its points at the ends of ParamArmLA (24 degrees about the
    shoulder) and ParamArmLB (55 degrees about the elbow)."""
    p = puppet()
    body = p["nodes"]["children"][0]
    up, lo = part("16_arm_l_up", -110, -80, -90, 60, 2), part("16_arm_l_lo", -110, 20, -90, 170, 2)
    sleeve = part("16_sleeve", -112, -82, -96, -40, 2)
    body["children"][1:1] = [up, lo, sleeve]
    shoulder, elbow = (-100.0, -80.0), (-100.0, 40.0)

    def turned_by(node, centre, degrees):
        pts = refine._pairs(node["mesh"]["verts"])
        if bend == "squash" and node is lo and centre == elbow:
            return [[0.0, (y - centre[1]) * 0.1 * degrees / 55.0] for x, y in pts]     # not a turn
        moved = refine.rotated(pts, centre, math.radians(degrees))
        return [[mx - x, my - y] for (x, y), (mx, my) in zip(pts, moved)]

    def keyed(node, centre, degrees, weight=1.0):
        rest = [[0.0, 0.0]] * (len(node["mesh"]["verts"]) // 2)
        ends = [turned_by(node, centre, -degrees * weight), turned_by(node, centre, degrees * weight)]
        return {"node": node["uuid"], "param_name": "deform", "values": [[ends[0]], [rest], [ends[1]]],
                "isSet": [[True]] * 3, "interpolate_mode": "Linear"}
    p["param"].append({"name": "ParamArmLA", "axis_points": [[0.0, 0.5, 1.0], [0.0]],
                       "bindings": [keyed(up, shoulder, 24), keyed(lo, shoulder, 24), keyed(sleeve, shoulder, 24, 0.5)]})
    p["param"].append({"name": "ParamArmLB", "axis_points": [[0.0, 0.5, 1.0], [0.0]],
                       "bindings": [keyed(lo, elbow, 55)]})
    return p, shoulder, elbow


def place(p, suffix, values):
    """A part's points at control values (-10 to 10), as Inochi2D places them: its own moved copies
    blended in straight lines, then each node above it turned about its own position."""
    def find(node, path):
        if node.get("type") == "Part" and node["name"].endswith(suffix):
            return path + [node]
        for child in node.get("children") or []:
            found = find(child, path + [node])
            if found:
                return found
    chain = find(p["nodes"], [])

    def at(param, binding):
        axis = param["axis_points"][0]
        position = (values.get(param["name"], 0.0) + 10.0) / 20.0
        for i in range(len(axis) - 1):
            if position <= axis[i + 1] or i == len(axis) - 2:
                t = min(max((position - axis[i]) / (axis[i + 1] - axis[i]), 0.0), 1.0)
                a, b = binding["values"][i][0], binding["values"][i + 1][0]
                if isinstance(a, list):
                    return [((1 - t) * u[0] + t * v[0], (1 - t) * u[1] + t * v[1]) for u, v in zip(a, b)]
                return (1 - t) * a + t * b
    pts = refine._pairs(chain[-1]["mesh"]["verts"])
    for param in p["param"]:
        for b in param["bindings"]:
            if b["node"] == chain[-1]["uuid"] and b["param_name"] == "deform":
                pts = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(pts, at(param, b))]
    for node in reversed(chain):
        turn = sum(at(param, b) for param in p["param"] for b in param["bindings"]
                   if b["node"] == node["uuid"] and b["param_name"] == "transform.r.z")
        tx, ty = node["transform"]["trans"][:2]
        c, s = math.cos(turn), math.sin(turn)
        pts = [(tx + c * x - s * y, ty + s * x + c * y) for x, y in pts]
    return pts


class ArmTest(unittest.TestCase):
    def setUp(self):
        self.p, self.shoulder, self.elbow = puppet_with_arm()
        self.original = copy.deepcopy(self.p)
        self.report = refine.refine_arms(self.p)["arms"]

    def point(self, suffix, target, values):
        """Where the part's point that sits at `target` at rest goes."""
        rest = place(self.p, suffix, {})
        i = min(range(len(rest)), key=lambda k: math.hypot(rest[k][0] - target[0], rest[k][1] - target[1]))
        return place(self.p, suffix, values)[i]

    def test_the_arm_hangs_from_a_node_that_turns_at_the_shoulder(self):
        self.assertEqual(sorted(self.report), ["l"])
        for got, want in zip(self.report["l"]["shoulder"] + self.report["l"]["elbow"], self.shoulder + self.elbow):
            self.assertAlmostEqual(got, want, places=1)
        node = next(n for n in refine._nodes(self.p["nodes"]) if n.get("name") == "arm_l")
        self.assertEqual([c["name"] for c in node["children"]], ["16_arm_l_up", "16_arm_l_lo"])
        kinds = {b["param_name"] for b in binding_list(self.p, "ParamArmLA") if b["node"] == node["uuid"]}
        self.assertEqual(kinds, {"transform.r.z"})

    def test_nothing_moves_at_rest(self):
        for suffix in ("arm_l_up", "arm_l_lo"):
            before = mesh_points(self.original, suffix)
            for (x0, y0), (x1, y1) in zip(before, place(self.p, suffix, {})):
                self.assertAlmostEqual(x0, x1, places=6)
                self.assertAlmostEqual(y0, y1, places=6)

    def test_the_elbow_holds_together_however_both_turn(self):
        for a in (-10, -3, 0, 4, 6, 10):
            for b in (-10, -9, -2.5, 0, 5, 8.5, 10):
                values = {"ParamArmLA": a, "ParamArmLB": b}
                up, lo = self.point("arm_l_up", self.elbow, values), self.point("arm_l_lo", self.elbow, values)
                self.assertLess(math.hypot(up[0] - lo[0], up[1] - lo[1]), 1e-6, values)

    def test_a_forearm_keeps_its_length_between_keys(self):
        rest = math.hypot(170 - 40, 10)
        for b in (0.7, 3.3, 7.9, -4.4):
            elbow = self.point("arm_l_lo", self.elbow, {"ParamArmLB": b})
            hand = self.point("arm_l_lo", (-110, 170), {"ParamArmLB": b})
            self.assertGreater(math.hypot(hand[0] - elbow[0], hand[1] - elbow[1]), rest - 1.0, b)

    def test_the_hand_goes_to_the_hip_and_up_to_wave(self):
        wrist = (-110.0, 170.0)
        length = math.hypot(*(e - s for e, s in zip(self.elbow, self.shoulder))) + math.hypot(10, 130)
        hip = (-80.0 + refine.HIP_INSET * length, wrist[1] - refine.HIP_UP * length)   # the torso's edge is at x=-80
        hand = self.point("arm_l_lo", wrist, {"ParamArmLA": refine.POSE_A["hip"], "ParamArmLB": refine.POSE_B["hip"]})
        self.assertLess(math.hypot(hand[0] - hip[0], hand[1] - hip[1]), 2.0, (hand, hip))
        elbow = self.point("arm_l_lo", self.elbow, {"ParamArmLA": refine.POSE_A["hip"]})
        self.assertLess(elbow[0], self.elbow[0] - 20, "the elbow points out")
        values = {"ParamArmLA": refine.POSE_A["wave"], "ParamArmLB": refine.POSE_B["wave"]}
        hand, elbow = self.point("arm_l_lo", wrist, values), self.point("arm_l_lo", self.elbow, values)
        self.assertLess(hand[1], elbow[1] - 100, "the forearm points up to wave")

    def test_keys_are_close_enough_for_straight_lines_between(self):
        turns = [v[0] for v in next(b for b in binding_list(self.p, "ParamArmLA")
                                    if b["param_name"] == "transform.r.z")["values"]]
        self.assertLessEqual(max(abs(b - a) for a, b in zip(turns, turns[1:])), refine.SWING_STEP + 1e-9)
        bend = binding(self.p, "ParamArmLB", "arm_l_lo")
        pts = refine._pairs(next(n for n in refine._nodes(self.p["nodes"]) if n.get("name") == "16_arm_l_lo")["mesh"]["verts"])
        angles = [refine.fit_rotation(pts, [(x + d[0], y + d[1]) for (x, y), d in zip(pts, v[0])])[1] if any(
            abs(c) > 1e-9 for d in v[0] for c in d) else 0.0 for v in bend["values"]]
        self.assertLessEqual(max(abs(b - a) for a, b in zip(angles, angles[1:])), refine.BEND_STEP + 1e-6)

    def test_parts_the_rigger_moved_with_the_arm_keep_their_movement(self):
        sleeve = binding(self.p, "ParamArmLA", "sleeve")
        axis = next(x for x in self.p["param"] if x["name"] == "ParamArmLA")["axis_points"][0]
        old = binding(self.original, "ParamArmLA", "sleeve")["values"]
        for position, column in ((0.0, 0), (0.5, 1), (1.0, 2)):
            i = axis.index(position)
            for got, want in zip(sleeve["values"][i][0][5], old[column][0][5]):
                self.assertAlmostEqual(got, want, places=3)

    def test_an_arm_the_rigger_did_not_turn_rigidly_is_left_alone(self):
        p, _, _ = puppet_with_arm(bend="squash")
        self.assertEqual(refine.refine_arms(p)["arms"], "left as rigged")
        self.assertFalse(any(n.get("name") == "arm_l" for n in refine._nodes(p["nodes"])))


def face_picture(marks=((-3.0, -32.0),), scale=2):
    """The test face's own picture: plain skin with small dark ovals (noses) at these points of the
    face part (its box is -50..50 by -100..0). Returns the file's picture section."""
    import io
    from PIL import Image, ImageDraw
    size = 100 * scale
    picture = Image.new("RGBA", (size, size), (240, 214, 196, 255))
    draw = ImageDraw.Draw(picture)
    for x, y in marks:
        cx, cy = (x + 50) * scale, (y + 100) * scale
        draw.ellipse((cx - 3 * scale, cy - 5 * scale, cx + 3 * scale, cy + 5 * scale), fill=(150, 100, 90, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return refine.write_texture_section([(0, out.getvalue())], b"")


def puppet_with_face_picture():
    p = puppet()
    for item in refine.walk_parts(p["nodes"]):
        if item["role"] == "face_base":
            verts = item["node"]["mesh"]["verts"]
            item["node"]["textures"] = [0, None, None]
            item["node"]["mesh"]["uvs"] = [((v + 50) / 100.0 if k % 2 == 0 else (v + 100) / 100.0)
                                           for k, v in enumerate(verts)]
    return p


class NoseTest(unittest.TestCase):
    """A nose drawn on the face's skin, a little to the left of the eyes' middle (as on a test picture
    whose turn to the right looked like a slide): found, and moved so both turns look alike."""

    def nose_and_eyes(self, p, position, nose_x=-3.0):
        """How far the nose's middle (drawn at nose_x) is to the right of the eyes' middle at this end of
        the turn. The skin's nearest point carries the nose."""
        index = {-1: 0, 0: 1, 1: 2}[position]
        pts = mesh_points(p, "face_base")
        k = min(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - nose_x, pts[i][1] + 132.0))
        nose = nose_x + binding(p, "ParamAngleX", "face_base")["values"][index][0][k][0]
        eyes = []
        for role in ("eye_l", "eye_r"):
            e = mesh_points(p, role)
            moves = binding(p, "ParamAngleX", role)["values"][index][0]
            eyes.append(sum(x + d[0] for (x, _), d in zip(e, moves)) / len(e))
        return nose - sum(eyes) / 2.0

    def test_the_mark_is_found_where_it_was_drawn(self):
        p = puppet_with_face_picture()
        g = refine.head_geometry(refine.walk_parts(p["nodes"]))
        nose = refine.find_nose(face_picture(), g)
        self.assertIsNotNone(nose)
        self.assertAlmostEqual(nose["x"], -3.0, delta=0.6)          # the face part sits 100 higher
        self.assertAlmostEqual(nose["y"], -132.0, delta=0.6)
        self.assertIsNone(refine.find_nose(face_picture(marks=()), g))
        self.assertIsNone(refine.find_nose(face_picture(marks=((22.0, -32.0),)), g))   # too far aside

    def test_without_its_picture_the_nose_lags_behind_a_turn_to_its_other_side(self):
        p = puppet_with_face_picture()
        refine.refine(p)
        self.assertLess(self.nose_and_eyes(p, 1), 0.0)              # turned right, still left of the eyes

    def test_both_turns_mirror_each_other(self):
        p = puppet_with_face_picture()
        report = refine.refine(p, face_picture())
        self.assertFalse(report["nose"]["own_part"])
        self.assertAlmostEqual(report["nose"]["drawn_aside"], -0.06, delta=0.02)
        x = report["nose"]["drawn_aside"] * 50.0                    # where it was found (the eyes' middle is 0)
        left, rest, right = (self.nose_and_eyes(p, k, x) for k in (-1, 0, 1))
        self.assertAlmostEqual(rest, x, delta=0.01)                 # as drawn
        self.assertGreater(right, 1.0)                              # leads the turn to the right
        self.assertLess(left, -1.0)                                 # and the turn to the left
        self.assertAlmostEqual(right, -left, delta=0.15)
        lead = refine.DEPTH["nose"] * math.sin(refine.TURN_ANGLE) * 70.0
        self.assertAlmostEqual(right, lead, delta=0.4)

    def test_only_the_skin_round_the_nose_changes(self):
        plain, nosed = puppet_with_face_picture(), puppet_with_face_picture()
        refine.refine(plain)
        refine.refine(nosed, face_picture())
        pts = mesh_points(plain, "face_base")
        for name in ("ParamAngleX", "ParamAngleY"):
            a = binding(plain, name, "face_base")["values"]
            b = binding(nosed, name, "face_base")["values"]
            self.assertEqual(a[1], b[1])                            # nothing moves at rest
            for (x, y), da, db in zip(pts, a[2][0], b[2][0]):
                if math.hypot(x + 3.0, y + 132.0) > 5.0 + 0.35 * 50 + 0.5:
                    self.assertEqual(da, db)
        for role in ("eye_l", "mouth", "hair_front"):
            self.assertEqual(binding(plain, "ParamAngleX", role)["values"],
                             binding(nosed, "ParamAngleX", role)["values"])

    def test_a_nose_drawn_far_aside_is_evened_only_partly(self):
        p = puppet_with_face_picture()
        refine.refine(p, face_picture(marks=((-12.0, -32.0),)))
        left, right = self.nose_and_eyes(p, -1, -12.0), self.nose_and_eyes(p, 1, -12.0)
        self.assertLess(left, -12.0)                                # never back against a turn
        self.assertGreater(right, -12.0 + 1.5 * refine.DEPTH["nose"] * math.sin(refine.TURN_ANGLE) * 70.0)
        self.assertLess(right, 0.0)                                 # still partly as drawn


if __name__ == "__main__":
    unittest.main()
