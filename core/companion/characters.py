"""The characters installed on the device.

Each character is unpacked from its checked `.char` package into its own
folder. The device's own choices for a character (which voice, its memory)
live beside it and are never written back into a package.

    characters/<id>/package/...   the files from the package
    characters/<id>/device.json   voice chosen on this device
    characters/<id>/animations/   full-body animations added for a 3D avatar (animations.py)
    memory/<id>/                  what the character remembers (memory.py)
"""
import datetime
import io
import json
import os
import posixpath
import shutil
import tempfile

from . import package, vrm
from .store import write_atomic


class Characters:
    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def _dir(self, cid):
        if not package.ID_PATTERN.fullmatch(cid or ""):
            raise KeyError(cid)
        return os.path.join(self.folder, cid)

    def ids(self):
        return sorted(d for d in os.listdir(self.folder)
                      if package.ID_PATTERN.fullmatch(d) and os.path.isfile(os.path.join(self.folder, d, "package", "manifest.json")))

    def manifest(self, cid):
        with open(os.path.join(self._dir(cid), "package", "manifest.json"), encoding="utf-8") as f:
            return json.load(f)

    def list(self):
        out = []
        for cid in self.ids():
            m = self.manifest(cid)
            out.append({
                "id": cid, "name": m.get("name", cid), "kind": m.get("kind"), "author": m.get("author", ""),
                "capabilities": m.get("capabilities", {}),
                "licence": m.get("licence", ""), "licence_note": m.get("licence_note", ""),
                "drawing": self.drawing(cid),
                "preview": any(os.path.exists(self.file(cid, n)) for n in ("preview.png", "preview.webp")),
            })
        return out

    def file(self, cid, name):
        """Path of a file inside an installed package. Names come from the package format, never from users."""
        return os.path.join(self._dir(cid), "package", *name.split("/"))

    def install(self, data, replace=False):
        """Check and install a package. Returns its id. Raises package.PackageError."""
        pkg = package.open_package(io.BytesIO(data))
        target = self._dir(pkg.id)
        if os.path.exists(target) and not replace:
            raise package.PackageError("a character with the id '%s' is already installed" % pkg.id)
        staging = tempfile.mkdtemp(dir=self.folder, prefix=".install-")
        try:
            for name, content in pkg.files.items():
                path = os.path.join(staging, "package", *name.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
            if os.path.exists(target):
                # The device's own choices for the character, and animations added for it, stay.
                kept = os.path.join(target, "device.json")
                if os.path.exists(kept):
                    shutil.copy(kept, os.path.join(staging, "device.json"))
                added = os.path.join(target, "animations")
                if os.path.isdir(added):
                    shutil.copytree(added, os.path.join(staging, "animations"))
                old = target + ".old"
                os.replace(target, old)
                os.replace(staging, target)
                shutil.rmtree(old, ignore_errors=True)
            else:
                os.replace(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        if not self.device_settings(pkg.id).get("arrived"):
            # The day it arrived, for its anniversaries (special_days.py); a restored backup brings the first one.
            self.set_device_settings(pkg.id, {"arrived": datetime.date.today().isoformat()})
        return pkg.id

    def delete(self, cid):
        shutil.rmtree(self._dir(cid))

    def persona(self, cid):
        with open(self.file(cid, "persona.md"), encoding="utf-8") as f:
            return f.read()

    def set_persona(self, cid, text):
        if len(text.encode("utf-8")) > package.MAX_TEXT:
            raise ValueError("the persona is too long")
        if package.contains_secret(text):
            raise ValueError("the persona appears to contain an API key; it was not saved")
        write_atomic(self.file(cid, "persona.md"), text)

    def drawing(self, cid):
        """How the character is drawn: "vrm" (3D), "live2d", "inochi2d", or "" if it has no drawing."""
        for kind, names in (("vrm", (package.VRM_MODEL,)), ("live2d", (package.LIVE2D_MODEL,)),
                            ("inochi2d", ("visual/puppet.inp", "visual/puppet.inx"))):
            if any(os.path.exists(self.file(cid, n)) for n in names):
                return kind
        return ""

    def expressions(self, cid):
        """The names of a character's own expressions (a Live2D-format model's, or a VRM avatar's)."""
        if os.path.exists(self.file(cid, package.VRM_MODEL)):
            try:
                return vrm.info(vrm.read_description(self.file(cid, package.VRM_MODEL)))["expressions"]
            except (OSError, ValueError):
                return []
        try:
            with open(self.file(cid, package.LIVE2D_MODEL), encoding="utf-8") as f:
                refs = json.load(f).get("FileReferences", {})
            return [e["Name"] for e in refs.get("Expressions") or [] if isinstance(e.get("Name"), str)]
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            return []

    def emotion_map(self, cid):
        """Which of the character's own expressions shows each emotion: the package's first
        guess, with the owner's choices on this device over it ("" means none)."""
        mapping = {}
        try:
            with open(self.file(cid, "visual/emotions.json"), encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                mapping.update({k: v for k, v in loaded.items() if isinstance(k, str) and isinstance(v, str)})
        except (OSError, ValueError):
            pass
        mapping.update(self.device_settings(cid).get("emotions") or {})
        known = set(self.expressions(cid))
        return {k: v for k, v in mapping.items() if v in known}

    def own_motions(self, cid):
        """A Live2D-format model's own idle motions (its motion groups whose names start with "idle"), by name."""
        names = []
        try:
            with open(self.file(cid, package.LIVE2D_MODEL), encoding="utf-8") as f:
                groups = (json.load(f).get("FileReferences") or {}).get("Motions") or {}
            for group, items in groups.items():
                if not (isinstance(group, str) and group.lower().startswith("idle") and isinstance(items, list)):
                    continue
                for item in items:
                    file = item.get("File") if isinstance(item, dict) else None
                    if isinstance(file, str) and file.endswith(".motion3.json"):
                        names.append(posixpath.basename(file)[:-len(".motion3.json")])
        except (OSError, ValueError, AttributeError, TypeError):
            return []
        return names

    def made_in_studio(self, cid):
        """Made from a picture with Character Studio (whose own idle loop the device's movement replaces)."""
        return os.path.exists(self.file(cid, "studio/report.json"))

    def own_motion_setting(self, cid):
        """How much of the model's own idle motions shows: "full", "gentle" or "off". Models made with
        Character Studio start "off" (the device's own movement is made for them); others "full"."""
        saved = self.device_settings(cid).get("own_motions")
        if saved in OWN_MOTIONS:
            return saved
        return "off" if self.made_in_studio(cid) else "full"

    def device_settings(self, cid):
        try:
            with open(os.path.join(self._dir(cid), "device.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def set_device_settings(self, cid, changes):
        current = self.device_settings(cid)
        current.update(changes)
        write_atomic(os.path.join(self._dir(cid), "device.json"), json.dumps(current, indent=1))
        return current

    def export(self, cid):
        """The character as a package again: its files only, never memories or device choices."""
        files = {}
        base = os.path.join(self._dir(cid), "package")
        for root, _, names in os.walk(base):
            for n in names:
                path = os.path.join(root, n)
                rel = os.path.relpath(path, base).replace(os.sep, "/")
                if rel == "manifest.json":
                    continue
                with open(path, "rb") as f:
                    files[rel] = f.read()
        return package.build(files, self.manifest(cid))


# How much of a model's own idle motions shows, for each choice on the settings page.
OWN_MOTIONS = {"full": 1.0, "gentle": 0.5, "off": 0.0}


def split_persona(text):
    """Persona text -> (system prompt, greeting). The greeting is the '## Greeting' section, if any."""
    lines = text.splitlines()
    greeting, body, in_greeting = [], [], False
    for line in lines:
        if line.strip().lower().startswith("## greeting"):
            in_greeting = True
            continue
        if in_greeting and line.startswith("#"):
            in_greeting = False
        (greeting if in_greeting else body).append(line)
    return "\n".join(body).strip(), " ".join(l.strip() for l in greeting if l.strip())
