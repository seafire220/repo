"""A support report: what someone helping with the device needs to know, and nothing private
(docs/28-user-guide.md).

In it: the software's version, the board, its memory, storage and temperature; the settings (which hold no
keys; the owner's name, birthday, town and days of their own are left out, only whether they are set is
kept); which keys are set (not the keys); the characters (names and kinds, not their personas); how much
each remembers (counts, not contents); updates; and the device's recent events and service logs with every
stored key removed and every word anyone said left out.
"""
import datetime
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile

from .store import redact
from .updates import current_version

# Events that carry what someone said or what the character answered: left out of the report.
SPOKEN = {"heard", "replied", "saying", "speaking first", "partial", "transcript", "said"}


def _read(path, limit=4096):
    try:
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", "replace").strip("\0 \n")
    except OSError:
        return ""


def _meminfo():
    out = {}
    for line in _read("/proc/meminfo", 8192).splitlines():
        name, _, value = line.partition(":")
        if name in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            out[name] = value.strip()
    return out


def facts(device):
    """The report's main part, as a dict."""
    data_dir = device.data_dir
    usage = shutil.disk_usage(data_dir)
    os_release = dict(line.split("=", 1) for line in _read("/etc/os-release").splitlines() if "=" in line)
    temperature = _read("/sys/class/thermal/thermal_zone0/temp")
    characters = []
    for cid in device.characters.ids():
        manifest = device.characters.manifest(cid)
        memory = device.memory(cid)
        characters.append({"id": cid, "name": manifest.get("name"), "kind": manifest.get("kind"),
                           "drawing": device.characters.drawing(cid), "capabilities": manifest.get("capabilities", {}),
                           "conversations": len(memory.sessions()), "memories": len(memory.memories()),
                           "diary_entries": len(memory.diary())})
    return {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": current_version(),
        "system": {"os": os_release.get("PRETTY_NAME", "").strip('"'), "kernel": platform.release(),
                   "board": _read("/proc/device-tree/model"), "python": sys.version.split()[0],
                   "uptime_hours": round(float((_read("/proc/uptime") or "0").split()[0]) / 3600, 1),
                   "service_minutes": round((time.time() - device.started) / 60, 1),
                   "temperature_c": round(int(temperature) / 1000, 1) if temperature.isdigit() else None,
                   "memory": _meminfo(),
                   "storage_gb": {"total": round(usage.total / 1e9, 1), "free": round(usage.free / 1e9, 1)}},
        "settings": private_kept_out(device.store.settings()),
        "keys_set": device.store.secret_status(),
        "characters": characters,
        "active": device.active_id(),
        "updates": device.updates.status(),
        "state": device.state,
    }


def events(device):
    """The device's recent events, without anything anyone said."""
    out = []
    for event in device.log:
        if event.get("kind") in SPOKEN:
            continue
        kept = {k: v for k, v in event.items() if k not in ("text", "words", "heard", "reply")}
        if event.get("kind") == "timer" and "label" in kept:
            kept["label"] = "(a label)"          # the owner's own words ("the dentist")
        out.append(kept)
    return out


def journal(device, lines=400):
    """The services' recent log lines, if the system lets the service read them, with keys removed."""
    try:
        done = subprocess.run(["journalctl", "-u", "companion-core", "-u", "companion-stage", "-u", "companion-update",
                               "-n", str(lines), "--no-pager", "-o", "short-iso"], capture_output=True, text=True,
                              timeout=20)
        text = done.stdout if done.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    return redact(text, device.store) or "(the service logs could not be read)"


def private_kept_out(settings):
    """The settings without what is personal: the owner's name and birthday, the weather's town (where they live),
    and their own special days; whether each is set is kept, and how many wake-ups there are and when."""
    out = json.loads(json.dumps(settings))
    owner = out.get("owner") or {}
    out["owner"] = {k: bool(owner.get(k)) for k in ("name", "birthday")}
    weather = out.get("weather") or {}
    weather["place"] = "chosen" if weather.get("place") else "the time zone's city"
    special = out.get("special_days") or {}
    special["dates"] = len(special.get("dates") or [])
    alarms = out.get("alarms") or {}
    alarms["wake_ups"] = [{k: w.get(k) for k in ("time", "days", "on")} for w in alarms.get("wake_ups") or []]
    return out


def report(device):
    """The support report, as a zip's bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.json", redact(json.dumps(facts(device), indent=1, default=str), device.store))
        z.writestr("events.json", redact(json.dumps(events(device), indent=1, default=str), device.store))
        z.writestr("service-logs.txt", journal(device))
        z.writestr("README.txt", "A support report from a character device (%s). It holds no API keys, no passwords and "
                                 "nothing anyone said: versions, the board, settings, which keys are set, the characters' "
                                 "names, how much each remembers, and recent events and logs.\n" % current_version())
    return out.getvalue()
