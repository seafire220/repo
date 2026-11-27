"""Make a gesture or idle action for the character device from a video of a person.

    python studio/record_motion.py VIDEO --name little_dance [--words "do a little dance"]
        [--kind gesture|idle] [--start 1.0] [--end 3.5] [--out little_dance.json]
        [--send companion.local [--password ...]]

Film yourself (or someone who agreed to it) facing the camera, head and shoulders or the upper body in
view, doing the gesture once, starting and ending still. The video goes through Google's MediaPipe face
and body landmark models, on this computer (Apache-2.0; downloaded once, about 10 MB). The landmarks
become the stage's controls: the head's turn, nod and tilt; where the eyes look; the brows, smile,
smiling eyes and mouth; the shoulders and the body's tilt; each arm at the shoulder and the
elbow (not the lean towards or away from the camera: one camera's depth is too unsure). They are smoothed (the 1-euro filter), measured from the pose at the start, eased in and out so
the clip starts and ends at rest, cut down to the keys that matter, and written as the motion library
writes a clip. The character moves the way the video shows it, side for side: a head turned towards the
right of the picture turns the character towards the right of its screen.

With --send, the clip goes to the device (its settings-page password is asked for if not given), where
it can be tried, changed and saved on the Animation tab like any other.

Needs: mediapipe, opencv-python-headless, numpy (studio/requirements-motion.txt).
"""
import argparse
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
from companion import motions  # noqa: E402  (the device's own check of a clip)

MODELS = {
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "pose_landmarker_lite.task":
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
}
DEFAULT_MODELS = os.path.join(os.path.expanduser("~"), ".cache", "companion-studio", "mediapipe")

# The landmarks-to-controls conversion is the device's own (core/companion/mocap.py), shared with gestures
# recorded on a phone; its names are kept here too.
from companion.mocap import (  # noqa: E402,F401
    BLENDSHAPES, CHEEK_LEFT, CHEEK_RIGHT, CHIN, EYE_LEFT, EYE_RIGHT, NOSE, POSE, body_controls, clip_from_frames,
    entry_for, face_controls, frame_controls, head_controls, shoulder_controls)


# ----------------------------------------------------------------------------------------------------
# The video
# ----------------------------------------------------------------------------------------------------

def models_folder(folder):
    os.makedirs(folder, exist_ok=True)
    for name, url in MODELS.items():
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            print("downloading %s (once)" % name, file=sys.stderr)
            tmp = path + ".part"
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, path)
    return folder


def frames_from_video(path, folder, start=0.0, end=None, every=1):
    """Per-frame controls from a video, and its frame rate."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    face = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=os.path.join(folder, "face_landmarker.task")),
        running_mode=vision.RunningMode.VIDEO, output_face_blendshapes=True, num_faces=1))
    body = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=os.path.join(folder, "pose_landmarker_lite.task")),
        running_mode=vision.RunningMode.VIDEO, num_poses=1))
    video = cv2.VideoCapture(path)
    if not video.isOpened():
        raise ValueError("could not read the video %s" % path)
    fps = video.get(cv2.CAP_PROP_FPS) or 30.0
    frames, index = [], 0
    try:
        while True:
            ok, image = video.read()
            if not ok:
                break
            t = index / fps
            index += 1
            if t < start or (end is not None and t > end) or (index - 1) % every:
                continue
            h, w = image.shape[:2]
            rgb = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            stamp = int(t * 1000)
            points = blend = pose = None
            found = face.detect_for_video(rgb, stamp)
            if found.face_landmarks:
                points = {i: (p.x * w, p.y * h) for i, p in enumerate(found.face_landmarks[0])}
                if found.face_blendshapes:
                    blend = {c.category_name: c.score for c in found.face_blendshapes[0]}
            seen = body.detect_for_video(rgb, stamp)
            if seen.pose_landmarks:
                marks = seen.pose_landmarks[0]
                pose = {name: (marks[i].x * w, marks[i].y * h, marks[i].z * w, marks[i].visibility)
                        for name, i in POSE.items()}
            frames.append(frame_controls(points, blend, pose))
    finally:
        video.release()
        face.close()
        body.close()
    return frames, fps / every


def send(address, password, name, entry):
    """Log in to the device's settings page and save the clip there."""
    base = "http://%s" % address
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method=method,
                                     headers={"X-Companion": "1", "Content-Type": "application/json"})
        try:
            return opener.open(req, timeout=60).read()
        except urllib.error.HTTPError as error:
            raise ValueError("the device answered %d: %s" % (error.code, error.read().decode(errors="replace")[:200]))

    call("POST", "/api/login", {"password": password})
    call("PUT", "/api/motions/%s" % name, entry)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("video")
    parser.add_argument("--name", required=True, help="lower-case letters and _, such as little_dance")
    parser.add_argument("--kind", choices=("gesture", "idle"), default="gesture")
    parser.add_argument("--words", help="what it means, for the conversation (a new gesture)")
    parser.add_argument("--start", type=float, default=0.0, help="seconds into the video to start")
    parser.add_argument("--end", type=float, help="seconds into the video to end")
    parser.add_argument("--keep-blinks", action="store_true", help="keep the person's blinks in the clip")
    parser.add_argument("--models", default=DEFAULT_MODELS, help="where MediaPipe's models are kept")
    parser.add_argument("--out", help="write the clip here (else it is printed)")
    parser.add_argument("--send", metavar="ADDRESS", help="save it on the device at this address")
    parser.add_argument("--password", help="the device's settings-page password (asked for if not given)")
    args = parser.parse_args(argv)
    frames, fps = frames_from_video(args.video, models_folder(args.models), args.start, args.end)
    detected = sum(1 for f in frames if f) / max(1, len(frames))
    if detected < 0.5:
        raise SystemExit("a person was found in only %d%% of the frames: film them facing the camera, in good light"
                         % round(100 * detected))
    entry = motions.clean(args.name, entry_for(clip_from_frames(frames, fps, args.keep_blinks), args.kind, args.words))
    text = json.dumps(entry, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    print("%s: %d tracks over %.1f s, from %d frames" % (args.name, len(entry["tracks"]),
                                                          max(k[-1][0] for k in entry["tracks"].values()), len(frames)),
          file=sys.stderr)
    if args.send:
        send(args.send, args.password or getpass.getpass("The device's settings-page password: "), args.name, entry)
        print("Saved on the device: try it on its settings page, Animation tab.", file=sys.stderr)


if __name__ == "__main__":
    main()
