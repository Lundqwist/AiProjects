"""Control a Samsung (Tizen, 2016+) TV on the local network.

- find():      scans the local /24 network for Samsung TVs (their REST API on port 8001)
- wake():      Wake-on-LAN; the TV needs Settings > General > Network > Expert Settings >
               "Power On with Mobile" switched on
- keys():      remote-control buttons over the TV's websocket API (port 8002). The first time,
               the TV asks to allow "AI Workbench"; the pairing token is kept in %APPDATA%.
- launch():    open an app;  watch(): wake if needed, open an app, type a channel number
"""
from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import socket
import ssl
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import websocket  # websocket-client

CLIENT_NAME = "AI Workbench"
TOKEN_FILE = Path(os.environ.get("APPDATA", Path.home())) / "AI Workbench" / "tv-token"

# Apps found on Samsung TVs; ids are Samsung's own. Built-in apps start natively, web apps by deep link.
APPS = {
    "Samsung TV Plus": ("com.samsung.tv.tvplus", "NATIVE_LAUNCH"),
    "YouTube": ("9Ur5IzDKqV.TizenYouTube", "DEEP_LINK"),
    "TV4 Play": ("wYESlsUoe4.TV4", "DEEP_LINK"),
    "Rakuten TV": ("vbUQClczfR.Wuakitv", "DEEP_LINK"),
    "Prime Video": ("org.tizen.primevideo-dummy", "NATIVE_LAUNCH"),
    "Apple TV": ("com.samsung.tv.aria-video", "NATIVE_LAUNCH"),
    "Home Lab dashboard": ("LunDashTV1.HomeLab", "DEEP_LINK"),
}


@dataclass
class TvInfo:
    ip: str
    name: str
    model: str
    mac: str
    state: str  # on, standby, off


def info(ip: str, timeout: float = 2.0) -> TvInfo | None:
    try:
        with urllib.request.urlopen(f"http://{ip}:8001/api/v2/", timeout=timeout) as r:
            d = json.load(r)
    except (OSError, ValueError):
        return None
    dev = d.get("device", {})
    name = d.get("name", "").replace("[TV] ", "")
    return TvInfo(ip, name, dev.get("modelName", ""), dev.get("wifiMac", ""), dev.get("PowerState", "on"))


def local_subnet() -> str:
    """The first three parts of this PC's LAN address, e.g. '192.168.1'."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the LAN interface
        return s.getsockname()[0].rsplit(".", 1)[0]
    finally:
        s.close()


def find(timeout: float = 0.8) -> list[TvInfo]:
    """Samsung TVs that are on (or in network standby) on the local /24 network."""
    base = local_subnet()
    with concurrent.futures.ThreadPoolExecutor(64) as pool:
        found = pool.map(lambda n: info(f"{base}.{n}", timeout), range(1, 255))
    return [t for t in found if t]


class SamsungTV:
    def __init__(self, ip: str, mac: str = ""):
        self.ip, self.mac = ip, mac

    # ---- state and power ----------------------------------------------------------------
    def state(self) -> str:
        t = info(self.ip, 2.0)
        return t.state if t else "off"

    def wake(self, wait: int = 60) -> bool:
        """Send Wake-on-LAN until the TV reports 'on'. Returns True when it's on."""
        if self.state() == "on":
            return True
        if not self.mac:
            raise RuntimeError("The TV's MAC address is unknown: use Find TV while the TV is on.")
        mac = bytes.fromhex(self.mac.replace(":", "").replace("-", ""))
        packet = b"\xff" * 6 + mac * 16
        broadcast = self.ip.rsplit(".", 1)[0] + ".255"
        end = time.time() + wait
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while time.time() < end:
                for dst in (broadcast, self.ip):
                    for port in (7, 9):
                        try:
                            s.sendto(packet, (dst, port))
                        except OSError:
                            pass
                if self.state() == "on":
                    return True
                time.sleep(1)
        return False

    def power_off(self) -> None:
        if self.state() == "on":
            self.keys(["KEY_POWER"])

    # ---- remote control -----------------------------------------------------------------
    def _connect(self, timeout: int = 30):
        token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""
        name = base64.b64encode(CLIENT_NAME.encode()).decode()
        url = f"wss://{self.ip}:8002/api/v2/channels/samsung.remote.control?name={name}"
        if token:
            url += f"&token={token}"
        # The TV uses a self-signed certificate on the LAN, so it can't be verified
        ws = websocket.create_connection(url, timeout=timeout, sslopt={"cert_reqs": ssl.CERT_NONE})
        msg = json.loads(ws.recv())  # waits while the TV shows its "Allow" prompt the first time
        if msg.get("event") != "ms.channel.connect":
            ws.close()
            raise RuntimeError("The TV didn't allow the connection. Allow 'AI Workbench' on the TV "
                               "(Settings > General > External Device Manager > Device Connection Manager).")
        new = msg.get("data", {}).get("token")
        if new and new != token:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(new)
        return ws

    def keys(self, keys: list[str], delay: float = 0.35) -> None:
        ws = self._connect()
        try:
            for key in keys:
                ws.send(json.dumps({"method": "ms.remote.control", "params": {
                    "Cmd": "Click", "DataOfCmd": key, "Option": "false", "TypeOfRemote": "SendRemoteKey"}}))
                time.sleep(delay)
        finally:
            ws.close()

    def launch(self, app: str) -> None:
        """app: a name from APPS or a raw app id."""
        app_id, action = APPS.get(app, (app, "NATIVE_LAUNCH" if app.startswith(("com.", "org.")) else "DEEP_LINK"))
        ws = self._connect()
        try:
            ws.send(json.dumps({"method": "ms.channel.emit", "params": {
                "event": "ed.apps.launch", "to": "host", "data": {"appId": app_id, "action_type": action}}}))
            time.sleep(0.5)
        finally:
            ws.close()

    def channel(self, number: str) -> None:
        digits = [c for c in str(number) if c.isdigit()]
        if not digits:
            raise ValueError(f"Not a channel number: {number}")
        self.keys([f"KEY_{d}" for d in digits] + ["KEY_ENTER"])

    def watch(self, app: str = "", channel: str = "", emit=lambda s: None) -> str:
        """Wake the TV if needed, open an app (optional), then type a channel (optional)."""
        if self.state() != "on":
            emit("Waking the TV…")
            if not self.wake():
                raise RuntimeError("The TV didn't wake up. Is 'Power On with Mobile' switched on?")
            time.sleep(6)  # it takes app commands only once it has finished starting
        if app:
            emit(f"Opening {app}…")
            self.launch(app)
            if channel:
                time.sleep(10)  # apps like Samsung TV Plus need a moment before they take a number
        if channel:
            emit(f"Channel {channel}…")
            self.channel(channel)
        return f"{app or 'TV'}{', channel ' + channel if channel else ''}"


VOLUME_KEYS = {"up": "KEY_VOLUP", "down": "KEY_VOLDOWN", "mute": "KEY_MUTE"}
