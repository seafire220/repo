"""Wi-Fi through NetworkManager's command-line tool, nmcli.

If the device has no network a short while after starting, it opens its own
Wi-Fi hotspot, shows the hotspot's name and password on its screen, and
serves the console there. The owner picks their home network in the
console; the device joins it and the hotspot closes.
"""
import asyncio
import secrets
import shutil

HOTSPOT_NAME = "companion-setup"


class NetworkError(Exception):
    pass


async def _run(*args, timeout=40):
    if not shutil.which(args[0]):
        raise NetworkError("%s is not installed" % args[0])
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise NetworkError("%s took too long" % args[0])
    if proc.returncode != 0:
        raise NetworkError(err.decode("utf-8", "replace").strip() or "%s failed" % args[0])
    return out.decode("utf-8", "replace")


def split_terse(line):
    """nmcli -t output: fields separated by ':' with '\\:' for a literal colon."""
    fields, current, escaped = [], "", False
    for ch in line:
        if escaped:
            current += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            fields.append(current)
            current = ""
        else:
            current += ch
    fields.append(current)
    return fields


def outgoing_address():
    """The address this machine would use on its home network (nothing is sent: this only picks the way out)."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        return "" if address.startswith("127.") or address == "0.0.0.0" else address
    except OSError:
        return ""
    finally:
        probe.close()


class WiFi:
    def __init__(self, run=_run, interface="wlan0"):
        self.run = run
        self.interface = interface
        self.hotspot = None

    async def connected(self):
        out = await self.run("nmcli", "-t", "-f", "STATE", "general")
        return out.strip().startswith("connected")

    async def address(self):
        """The device's address on the home network: its Wi-Fi's, else the connection in use (an Ethernet
        cable: a TV box, or a Pi with one)."""
        try:
            out = await self.run("nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", self.interface)
        except NetworkError:
            out = ""
        for line in out.splitlines():
            value = split_terse(line)[-1]
            if "/" in value:
                return value.split("/")[0]
        return outgoing_address()

    async def networks(self):
        await self.run("nmcli", "device", "wifi", "rescan", "ifname", self.interface, timeout=15)
        out = await self.run("nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                             "ifname", self.interface)
        best = {}
        for line in out.splitlines():
            ssid, signal, security = (split_terse(line) + ["", "", ""])[:3]
            if not ssid:
                continue
            signal = int(signal) if signal.isdigit() else 0
            if ssid not in best or best[ssid]["signal"] < signal:
                best[ssid] = {"ssid": ssid, "signal": signal, "secure": security not in ("", "--")}
        return sorted(best.values(), key=lambda n: -n["signal"])

    async def join(self, ssid, password):
        if not ssid or len(ssid) > 32:
            raise NetworkError("a Wi-Fi name is 1 to 32 characters")
        if password and not 8 <= len(password) <= 63:
            raise NetworkError("a Wi-Fi password is 8 to 63 characters")
        args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", self.interface]
        if password:
            args += ["password", password]
        await self.stop_hotspot()
        await self.run(*args, timeout=60)

    async def saved_networks(self):
        """Names of the Wi-Fi networks the device has joined before (not its own hotspot)."""
        out = await self.run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show")
        names = []
        for line in out.splitlines():
            name, kind = (split_terse(line) + ["", ""])[:2]
            if kind.endswith("wireless") and name != HOTSPOT_NAME:
                names.append(name)
        return names

    async def start_hotspot(self, suffix):
        password = "".join(secrets.choice("23456789") for _ in range(8))
        ssid = "Companion-%s" % suffix
        await self.run("nmcli", "device", "wifi", "hotspot", "ifname", self.interface, "con-name", HOTSPOT_NAME,
                       "ssid", ssid, "password", password)
        self.hotspot = (ssid, password)
        return self.hotspot

    async def stop_hotspot(self):
        if self.hotspot is None:
            return
        try:
            await self.run("nmcli", "connection", "down", HOTSPOT_NAME)
        except NetworkError:
            pass
        self.hotspot = None
