"""The tracking a phone runs when it records a gesture (phone_record.py): MediaPipe for the web and its face
and body models, which the device gives to the phone's browser. All Apache License 2.0 (Google), at fixed
versions, each file checked against its SHA-256.

The installer fetches them for the card image; a device that got phone recording through an update fetches
them the first time it is asked for them. Nothing else uses them.

    python3 -m companion.mocap_assets FOLDER      (fetch whatever is missing into FOLDER)
"""
import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

TASKS_VISION = "https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-1.2.3.tgz"
TASKS_VISION_SHA256 = "469602650dab70256df387983ea44e85fa66020335bae85a54fa8fda062ca0a0"
# name served to the phone: (where it comes from, its path in the package or None for a plain download, SHA-256)
FILES = {
    "vision_bundle.mjs": (TASKS_VISION, "package/vision_bundle.mjs",
                          "fb0c02c296f44ce0cc324ba16bd646d8773eb4a7847db4019cc4d0430db7a9b5"),
    "vision_wasm_internal.js": (TASKS_VISION, "package/wasm/vision_wasm_internal.js",
                                "2aa5fd3fdd3d8208cd7587df0bb19fb6925c4ea16a76dd025aedc1f64d6a6309"),
    "vision_wasm_internal.wasm": (TASKS_VISION, "package/wasm/vision_wasm_internal.wasm",
                                  "225bc06c0f51a9cf31fea0e9e0b0243e9e01fb5ef332cc1a7fa6656dbdfdaabf"),
    "face_landmarker.task": (
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
        None, "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"),
    "pose_landmarker_lite.task": (
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
        None, "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a"),
}
SIZE_MB = 24
NOTICE = """MediaPipe for the web (@mediapipe/tasks-vision 1.2.3) and its face landmarker and pose landmarker
(lite) models, by Google, under the Apache License, Version 2.0 (the model cards say the same). The device
gives them to a phone's browser when it records a gesture; they are used unchanged. The licence's text is
among the screen program's licences, and in /usr/share/common-licenses/Apache-2.0.
"""
CONTENT_TYPES = {".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm",
                 ".task": "application/octet-stream"}


def missing(folder):
    """The files not yet in `folder` (by name; their contents were checked when they were put there)."""
    return [name for name in FILES if not os.path.isfile(os.path.join(folder, name))]


def _get(url, opener=urllib.request.urlopen):
    with opener(url, timeout=120) as response:
        return response.read()


def fetch(folder, opener=urllib.request.urlopen):
    """Fetch the missing files into `folder`, each checked; nothing is put in place unless all are right."""
    need = missing(folder)
    if not need:
        return []
    downloads = {}
    # (On a card image the models the device came with are on the read-only system, so the data partition's
    # models folder may not be there yet.)
    parent = os.path.dirname(os.path.abspath(folder))
    os.makedirs(parent, exist_ok=True)
    staged = tempfile.mkdtemp(prefix=".mocap-", dir=parent)
    try:
        for name in need:
            url, member, digest = FILES[name]
            if url not in downloads:
                data = _get(url, opener)
                if url == TASKS_VISION and hashlib.sha256(data).hexdigest() != TASKS_VISION_SHA256:
                    raise ValueError("MediaPipe for the web is not the version checked (its SHA-256 differs)")
                downloads[url] = data
            data = downloads[url]
            if member:
                with tarfile.open(fileobj=io.BytesIO(downloads[url])) as tar:
                    data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("%s is not the file checked (its SHA-256 differs)" % name)
            with open(os.path.join(staged, name), "wb") as f:
                f.write(data)
        os.makedirs(folder, exist_ok=True)
        for name in need:
            os.replace(os.path.join(staged, name), os.path.join(folder, name))
        with open(os.path.join(folder, "LICENSE"), "w", encoding="utf-8") as f:
            f.write(NOTICE)
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return need


def main(argv):
    if len(argv) != 1:
        raise SystemExit(__doc__.split("\n\n")[2].strip())
    got = fetch(argv[0])
    print("fetched %s" % (", ".join(got) if got else "nothing: all there"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
