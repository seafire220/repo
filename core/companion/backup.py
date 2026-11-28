"""Backups: everything the owner made on the device, in one file, to keep, or to move to another device or
a bigger card (docs/28-user-guide.md).

In a backup: each character (as the package it was added as), its device choices (voice, movement,
temperament...) and its own animations; everything it remembers (conversations, memories, diary, the owner's
notes); the settings; gestures changed or added on the settings page.

Never in a backup: API keys and the settings-page password (they are put in again on the new device), saved
Wi-Fi networks, the software, and its models.

A restore goes through the same checks as adding things by hand: each character is installed as an upload
is, each animation checked as an added one is, memory files are accepted only by their exact names, and
settings only through the settings' own checks. So a backup file, even a tampered one, can do no more than
the settings page can.
"""
import datetime
import json
import os
import re
import shutil
import zipfile

from . import animations as animations_module
from . import package
from .store import DEFAULT_SETTINGS, write_atomic
from .updates import current_version

FORMAT = 1
MAX_BACKUP = 2 * 1024 * 1024 * 1024           # bytes, unpacked
MEMORY_FILES = re.compile(r"(history|sessions|memories|diary)\.jsonl|notes\.md|state\.json|archive/\d{4}-\d{2}\.jsonl")
MAX_MEMORY_FILE = 64 * 1024 * 1024
ID = re.compile(r"[a-z0-9][a-z0-9\-]{0,47}")
ROLE = re.compile(r"[a-z][a-z0-9_]{0,39}")


class BackupError(Exception):
    pass


def make(device, path):
    """Write a backup of the device to `path` (a zip). Returns what is in it."""
    info = {"format": FORMAT, "product": "companion", "version": current_version(),
            "created": datetime.datetime.now().isoformat(timespec="seconds"), "active": device.active_id(),
            "characters": []}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for cid in device.characters.ids():
            z.writestr("characters/%s.char" % cid, device.characters.export(cid))
            z.writestr("characters/%s/device.json" % cid, json.dumps(device.characters.device_settings(cid), indent=1))
            index = device.animations.index(cid)
            folder = device.animations.folder(cid)
            for role in index:
                clip = os.path.join(folder, role + ".vrma")
                if os.path.isfile(clip):
                    z.write(clip, "characters/%s/animations/%s.vrma" % (cid, role))
            if index:
                z.writestr("characters/%s/animations.json" % cid, json.dumps(index, indent=1))
            memory = os.path.join(device.memory_dir, cid)
            remembered = 0
            for root, _, names in os.walk(memory):
                for name in sorted(names):
                    rel = os.path.relpath(os.path.join(root, name), memory).replace(os.sep, "/")
                    if MEMORY_FILES.fullmatch(rel):
                        z.write(os.path.join(root, name), "memory/%s/%s" % (cid, rel))
                        remembered += 1
            info["characters"].append({"id": cid, "name": device.characters.manifest(cid).get("name", cid),
                                       "memory_files": remembered, "animations": len(index)})
        pictures = device.backgrounds.index()
        for picture in pictures:
            z.write(os.path.join(device.backgrounds.folder, picture + ".jpg"), "backgrounds/%s.jpg" % picture)
        if pictures:
            z.writestr("backgrounds/backgrounds.json", json.dumps(pictures, indent=1))
        info["backgrounds"] = len(pictures)
        z.writestr("settings.json", json.dumps(device.store.settings(), indent=1))
        custom = device.custom_motions.all()
        if custom:
            z.writestr("custom-motions.json", json.dumps(custom, indent=1))
        z.writestr("backup.json", json.dumps(info, indent=1))
    return info


def _allowed(name, characters):
    if name in ("backup.json", "settings.json", "custom-motions.json", "backgrounds/backgrounds.json"):
        return True
    m = re.fullmatch(r"backgrounds/([a-z0-9_]{1,48})\.jpg", name)
    if m:
        return True
    m = re.fullmatch(r"characters/([^/]+)(\.char|/device\.json|/animations\.json|/animations/([^/]+)\.vrma)", name)
    if m:
        return m.group(1) in characters and (m.group(3) is None or bool(ROLE.fullmatch(m.group(3))))
    m = re.fullmatch(r"memory/([^/]+)/(.+)", name)
    return bool(m) and m.group(1) in characters and bool(MEMORY_FILES.fullmatch(m.group(2)))


def restore(device, path):
    """Put a backup's contents on the device: its characters (replacing any with the same id), their memory,
    choices and animations, the settings and the changed gestures. Returns what was done, with anything
    that could not be restored and why. Raises BackupError if the file is not a backup."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise BackupError("this is not a backup file")
    with archive:
        try:
            info = json.loads(archive.read("backup.json"))
        except (KeyError, ValueError):
            raise BackupError("this is not a backup file (it has no backup.json)")
        if not isinstance(info, dict) or info.get("product") != "companion" or info.get("format") != FORMAT:
            raise BackupError("this backup is not from this kind of device, or is from a newer one")
        characters = [c.get("id") for c in info.get("characters", []) if isinstance(c, dict)
                      and isinstance(c.get("id"), str) and ID.fullmatch(c["id"])]
        members = archive.infolist()
        if sum(m.file_size for m in members) > MAX_BACKUP:
            raise BackupError("the backup is too large")
        for m in members:
            if m.is_dir():
                continue
            if not _allowed(m.filename, characters):
                raise BackupError("the backup holds something a backup does not (%s); it was refused" % m.filename[:80])
            if m.compress_size and m.file_size / max(m.compress_size, 1) > 500:
                raise BackupError("the backup is suspiciously compressed; it was refused")
        names = {m.filename for m in members}
        done = {"characters": [], "skipped": []}
        for cid in characters:
            try:
                installed = device.characters.install(archive.read("characters/%s.char" % cid), replace=True)
            except (KeyError, package.PackageError) as error:
                done["skipped"].append("%s: %s" % (cid, error))
                continue
            _restore_choices(device, archive, names, installed)
            clips = _restore_animations(device, archive, names, cid, installed, done)
            remembered = _restore_memory(device, archive, names, cid, installed)
            done["characters"].append({"id": installed, "memory_files": remembered, "animations": clips})
        try:
            settings = json.loads(archive.read("settings.json")) if "settings.json" in names else {}
            settings = {k: v for k, v in settings.items() if k in DEFAULT_SETTINGS} if isinstance(settings, dict) else {}
            if settings:
                device.store.update_settings(settings)
        except (ValueError, TypeError) as error:
            done["skipped"].append("settings: %s" % error)
        if "custom-motions.json" in names:
            try:
                for name, entry in json.loads(archive.read("custom-motions.json")).items():
                    try:
                        device.custom_motions.set(name, entry)
                    except (ValueError, TypeError, KeyError) as error:
                        done["skipped"].append("gesture %s: %s" % (name, error))
            except (ValueError, AttributeError):
                done["skipped"].append("the changed gestures could not be read")
        _restore_backgrounds(device, archive, names, done)
    active = info.get("active")
    if active in device.characters.ids():
        device.store.update_settings({"active_character": active})
    return done


def _restore_choices(device, archive, names, cid):
    source = "characters/%s/device.json" % cid
    if source in names:
        try:
            choices = json.loads(archive.read(source))
            if isinstance(choices, dict):
                device.characters.set_device_settings(cid, choices)
        except ValueError:
            pass


def _restore_animations(device, archive, names, source_id, cid, done):
    index = {}
    if "characters/%s/animations.json" % source_id in names:
        try:
            index = json.loads(archive.read("characters/%s/animations.json" % source_id))
        except ValueError:
            index = {}
    count = 0
    prefix = "characters/%s/animations/" % source_id
    for name in sorted(n for n in names if n.startswith(prefix)):
        role = name[len(prefix):-len(".vrma")]
        entry = index.get(role) if isinstance(index.get(role), dict) else {}
        try:
            device.animations.add(cid, role, archive.read(name), title=entry.get("title", ""), words=entry.get("words"))
            count += 1
        except animations_module.AnimationError as error:
            done["skipped"].append("%s's animation %s: %s" % (cid, role, error))
    return count


def _restore_memory(device, archive, names, source_id, cid):
    """The character's memory files, replacing what it remembers now."""
    prefix = "memory/%s/" % source_id
    files = [n for n in names if n.startswith(prefix)]
    if not files:
        return 0
    folder = os.path.join(device.memory_dir, cid)
    shutil.rmtree(folder, ignore_errors=True)
    for name in files:
        rel = name[len(prefix):]
        info = archive.getinfo(name)
        if info.file_size > MAX_MEMORY_FILE:
            continue
        target = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        write_atomic(target, archive.read(name))
    return len(files)


def _restore_backgrounds(device, archive, names, done):
    """The owner's background pictures, under the names they had (the characters' choices name them)."""
    if "backgrounds/backgrounds.json" not in names:
        return
    import io
    from PIL import Image
    from .backgrounds import ID, MAX_PICTURES
    from .store import write_atomic
    try:
        saved = json.loads(archive.read("backgrounds/backgrounds.json"))
    except (ValueError, KeyError):
        done["skipped"].append("the background pictures could not be read")
        return
    index = device.backgrounds.index()
    os.makedirs(device.backgrounds.folder, exist_ok=True)
    for picture, entry in (saved.items() if isinstance(saved, dict) else []):
        name = "backgrounds/%s.jpg" % picture
        if not ID.fullmatch(picture) or not isinstance(entry, dict) or name not in names:
            continue
        if picture not in index and len(index) >= MAX_PICTURES:
            done["skipped"].append("background %s: there are already %d pictures" % (picture, MAX_PICTURES))
            continue
        data = archive.read(name)
        try:
            with Image.open(io.BytesIO(data)) as im:
                if im.format != "JPEG":
                    raise ValueError("not a JPEG")
                im.verify()
        except (OSError, ValueError) as error:
            done["skipped"].append("background %s: %s" % (picture, error))
            continue
        write_atomic(os.path.join(device.backgrounds.folder, picture + ".jpg"), data)
        index[picture] = {"title": str(entry.get("title") or "My picture")[:60], "added": int(entry.get("added") or 0)}
    write_atomic(device.backgrounds.index_path, json.dumps(index, indent=1))
