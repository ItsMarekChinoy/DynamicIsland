#!/usr/bin/env python3
"""
Dynamic Island for Laptop  v4
=============================

Single-file iPhone Dynamic Island clone for the top of your laptop screen.

Triggered by hovering the top-middle edge of the screen, near your camera.

NEW IN v4
---------
• Themes  —  7 built-in colour schemes (right-click → Theme).
              Persisted to ~/.dynamic_island_settings.json
• Volume control  —  click anywhere on the volume bar in the System / Audio
              card, scroll on the bar, or use the menu's Volume Up / Down.
• Mute & volume pill animations  —  whenever your system volume or mute
              state changes (from anywhere — keyboard keys, the island,
              another app), a pill animation slides down with a speaker
              icon and a live level bar.
• File holder  —  drag any file from Explorer / Finder onto the island.
              It expands and holds the files. Switch to the Files card and
              drag a row OUT — drop it into a browser upload field, an
              email compose window, Discord, anywhere.

ALREADY IN v3
-------------
Clipboard pill, sound alerts on timer, quick notes, timer presets,
battery time-remaining, network throughput, pin mode, low-battery warning.

ALREADY IN v2
-------------
Media controls (play / pause / skip), live progress, stopwatch,
weather card, system stats, scroll-wheel card switcher, global hotkey
(Ctrl+Shift+Space), auto-hide on fullscreen.

QUICK START
-----------
    python dynamic_island.py

First run pip-installs PyQt6, psutil, (winsdk + pycaw on Windows,
keyboard if available).

PLATFORMS
---------
Windows : full media + album art via SMTC, volume via pycaw (+ keybd_event)
macOS   : Spotify / Apple Music via AppleScript, volume via osascript
Linux   : MPRIS via playerctl, volume via pactl or amixer

TUNE
----
Edit constants in the "Tunables" section — sizes, hot zone,
notes path, low-battery threshold, etc.
"""

# ─────────────────────────────────────────────────────────────────────
#   Dependency bootstrap
# ─────────────────────────────────────────────────────────────────────
import sys
import platform
import subprocess


def _ensure(pkg, import_name=None):
    try:
        __import__(import_name or pkg)
    except ImportError:
        print(f"[setup] installing {pkg} …")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "--quiet"]
        )


_ensure("PyQt6", "PyQt6.QtWidgets")
_ensure("psutil")
SYSTEM = platform.system()
if SYSTEM == "Windows":
    try:
        _ensure("winsdk")
    except Exception:
        print("[setup] winsdk unavailable - Spotify/media display disabled")
        print("[setup] (winsdk needs Python 3.12 - on 3.14 it tries to compile from source)")
    try:
        _ensure("pycaw")
        _ensure("comtypes")
    except Exception:
        print("[setup] pycaw unavailable - volume slider disabled, only volume keys will work")

try:
    _ensure("keyboard")
    import keyboard as kb  # noqa: F401
    HAS_HOTKEY = True
except Exception:
    HAS_HOTKEY = False


# ─────────────────────────────────────────────────────────────────────
#   Imports
# ─────────────────────────────────────────────────────────────────────
import asyncio
import calendar as _cal
import json
import math
import os
import time
import urllib.request
from datetime import datetime, date, timedelta
from enum import Enum

import psutil

from PyQt6.QtCore import (
    QEasingCurve,
    QMimeData,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QDrag,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygon,
)
from PyQt6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QSystemTrayIcon,
    QWidget,
)


# ─────────────────────────────────────────────────────────────────────
#   Tunables
# ─────────────────────────────────────────────────────────────────────
HOTZONE_W       = 280
HOTZONE_H       = 12
PILL_W, PILL_H  = 320, 38
EXPAND_W, EXPAND_H = 420, 178
TOP_MARGIN      = 6

# Hidden state geometry — only used as the off-screen "parking" rect while
# the widget is hide()'n. The widget is fully invisible in HIDDEN state,
# but the rect still has to be valid (zero-area rects upset Qt animations).
HIDDEN_W, HIDDEN_H = 320, 6

ANIM_MS         = 380
HOVER_POLL_MS   = 50
MEDIA_POLL_MS   = 1500
SYSTEM_POLL_MS  = 1500
AUDIO_POLL_MS   = 350
FULLSCREEN_POLL_MS = 1500
WEATHER_REFRESH_S = 30 * 60
HIDE_DELAY_MS   = 800
NOTIF_DURATION_S = 4
LOW_BATTERY_PCT = 20
CLIP_PREVIEW_LEN = 50
CLIP_PILL_DURATION = 2.5
VOL_PILL_DURATION = 1.6
NOTES_PATH    = os.path.expanduser("~/.dynamic_island_notes.json")
EVENTS_PATH   = os.path.expanduser("~/.dynamic_island_events.json")
SETTINGS_PATH = os.path.expanduser("~/.dynamic_island_settings.json")
NOTES_VISIBLE = 3
NOTES_MAX = 30
FILES_VISIBLE = 3
FILES_MAX = 12
EVENTS_MAX = 500
EVENTS_UPCOMING = 3

VOLUME_STEP = 0.05  # 5% nudge per scroll click / menu press

# Accent slots a user can pick for the visualizer / progress bar / etc.
# Each maps to an attribute on the active Theme, so the same accent name
# follows the theme's actual palette when you switch themes.
ACCENT_NAMES = ["green", "amber", "red", "blue", "purple", "cyan", "pink"]


class State(Enum):
    HIDDEN = 0
    PILL   = 1
    EXPAND = 2


class Card(Enum):
    AUTO     = 0   # smart pick (media if playing, else clock)
    MEDIA    = 1
    CLOCK    = 2
    CALENDAR = 3
    SYSTEM   = 4
    TIMERS   = 5
    NOTES    = 6
    AUDIO    = 7
    FILES    = 8
    SETTINGS = 9


CARDS_ORDER = [Card.MEDIA, Card.CLOCK, Card.CALENDAR, Card.SYSTEM, Card.AUDIO,
               Card.TIMERS, Card.NOTES, Card.FILES, Card.SETTINGS]


# ─────────────────────────────────────────────────────────────────────
#   Themes
# ─────────────────────────────────────────────────────────────────────
class Theme:
    """Container for every colour the island ever paints."""
    __slots__ = (
        "name", "label",
        "bg",                 # main pill / expanded fill
        "bg_inner",           # inner panels (buttons, bars)
        "bg_track",           # progress / volume tracks
        "border",             # subtle hairline color
        "text", "text_dim", "text_faint",
        "amber", "green", "red", "blue", "purple", "cyan", "pink",
        "drop_glow",          # color used when a drag-from-outside enters
    )

    def __init__(self, name, label, **kw):
        self.name = name
        self.label = label
        for k, v in kw.items():
            setattr(self, k, v)


def _rgb(r, g, b, a=255):
    return QColor(r, g, b, a)


THEMES = {
    "classic": Theme(
        "classic", "Classic Black",
        bg=_rgb(0, 0, 0),
        bg_inner=_rgb(35, 35, 35),
        bg_track=_rgb(45, 45, 45),
        border=_rgb(60, 60, 60),
        text=_rgb(255, 255, 255),
        text_dim=_rgb(170, 170, 170),
        text_faint=_rgb(120, 120, 120),
        amber=_rgb(255, 159, 10),
        green=_rgb(120, 220, 120),
        red=_rgb(255, 90, 90),
        blue=_rgb(80, 160, 255),
        purple=_rgb(180, 130, 255),
        cyan=_rgb(80, 220, 220),
        pink=_rgb(255, 120, 200),
        drop_glow=_rgb(120, 220, 120),
    ),
    "midnight": Theme(
        "midnight", "Midnight Blue",
        bg=_rgb(10, 16, 32),
        bg_inner=_rgb(28, 38, 62),
        bg_track=_rgb(40, 52, 80),
        border=_rgb(60, 80, 110),
        text=_rgb(230, 240, 255),
        text_dim=_rgb(150, 170, 200),
        text_faint=_rgb(100, 120, 150),
        amber=_rgb(255, 200, 90),
        green=_rgb(140, 220, 200),
        red=_rgb(255, 110, 130),
        blue=_rgb(120, 180, 255),
        purple=_rgb(180, 150, 255),
        cyan=_rgb(120, 230, 240),
        pink=_rgb(255, 140, 220),
        drop_glow=_rgb(120, 180, 255),
    ),
    "forest": Theme(
        "forest", "Forest",
        bg=_rgb(12, 22, 16),
        bg_inner=_rgb(28, 44, 34),
        bg_track=_rgb(40, 60, 46),
        border=_rgb(60, 86, 68),
        text=_rgb(235, 245, 232),
        text_dim=_rgb(170, 195, 170),
        text_faint=_rgb(115, 140, 118),
        amber=_rgb(240, 200, 110),
        green=_rgb(140, 220, 120),
        red=_rgb(240, 130, 100),
        blue=_rgb(140, 200, 220),
        purple=_rgb(200, 180, 230),
        cyan=_rgb(140, 230, 200),
        pink=_rgb(240, 180, 200),
        drop_glow=_rgb(140, 220, 120),
    ),
    "sunset": Theme(
        "sunset", "Sunset",
        bg=_rgb(30, 14, 18),
        bg_inner=_rgb(58, 30, 36),
        bg_track=_rgb(78, 42, 50),
        border=_rgb(110, 60, 70),
        text=_rgb(255, 240, 232),
        text_dim=_rgb(220, 180, 170),
        text_faint=_rgb(160, 120, 115),
        amber=_rgb(255, 170, 80),
        green=_rgb(180, 230, 140),
        red=_rgb(255, 110, 110),
        blue=_rgb(180, 200, 255),
        purple=_rgb(220, 160, 230),
        cyan=_rgb(220, 200, 240),
        pink=_rgb(255, 130, 180),
        drop_glow=_rgb(255, 170, 80),
    ),
    "cyberpunk": Theme(
        "cyberpunk", "Cyberpunk",
        bg=_rgb(8, 6, 18),
        bg_inner=_rgb(30, 22, 50),
        bg_track=_rgb(50, 36, 78),
        border=_rgb(120, 60, 200),
        text=_rgb(240, 230, 255),
        text_dim=_rgb(200, 170, 240),
        text_faint=_rgb(140, 110, 180),
        amber=_rgb(255, 220, 100),
        green=_rgb(120, 255, 180),
        red=_rgb(255, 80, 130),
        blue=_rgb(120, 200, 255),
        purple=_rgb(220, 120, 255),
        cyan=_rgb(80, 240, 240),
        pink=_rgb(255, 80, 200),
        drop_glow=_rgb(80, 240, 240),
    ),
    "ocean": Theme(
        "ocean", "Ocean",
        bg=_rgb(8, 24, 32),
        bg_inner=_rgb(20, 50, 64),
        bg_track=_rgb(34, 70, 86),
        border=_rgb(60, 110, 130),
        text=_rgb(230, 250, 255),
        text_dim=_rgb(150, 200, 220),
        text_faint=_rgb(100, 145, 165),
        amber=_rgb(255, 200, 110),
        green=_rgb(120, 230, 200),
        red=_rgb(255, 130, 130),
        blue=_rgb(120, 200, 240),
        purple=_rgb(180, 180, 240),
        cyan=_rgb(110, 230, 230),
        pink=_rgb(255, 160, 220),
        drop_glow=_rgb(110, 230, 230),
    ),
    "mono_light": Theme(
        "mono_light", "Mono Light",
        bg=_rgb(245, 245, 247),
        bg_inner=_rgb(220, 220, 224),
        bg_track=_rgb(200, 200, 206),
        border=_rgb(180, 180, 188),
        text=_rgb(20, 20, 22),
        text_dim=_rgb(80, 80, 86),
        text_faint=_rgb(140, 140, 146),
        amber=_rgb(220, 130, 0),
        green=_rgb(40, 160, 80),
        red=_rgb(220, 60, 60),
        blue=_rgb(40, 110, 220),
        purple=_rgb(140, 80, 220),
        cyan=_rgb(20, 150, 170),
        pink=_rgb(220, 80, 160),
        drop_glow=_rgb(40, 110, 220),
    ),
}

THEME_ORDER = ["classic", "midnight", "forest", "sunset",
               "cyberpunk", "ocean", "mono_light"]


# ─────────────────────────────────────────────────────────────────────
#   Settings (theme + alert mute, persisted)
# ─────────────────────────────────────────────────────────────────────
def load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
#   Models
# ─────────────────────────────────────────────────────────────────────
class MediaInfo:
    __slots__ = (
        "title", "artist", "is_playing", "art",
        "position_s", "duration_s", "position_t",
    )

    def __init__(self, title="", artist="", is_playing=False, art=None,
                 position_s=0.0, duration_s=0.0, position_t=0.0):
        self.title = title
        self.artist = artist
        self.is_playing = is_playing
        self.art = art
        self.position_s = position_s
        self.duration_s = duration_s
        self.position_t = position_t

    def is_active(self):
        return bool(self.title) and self.is_playing

    def current_position(self):
        if not self.duration_s:
            return 0.0
        if self.is_playing:
            elapsed = time.time() - self.position_t
            return min(self.duration_s, self.position_s + elapsed)
        return self.position_s


class WeatherInfo:
    __slots__ = ("temp_c", "desc", "fetched_at")

    def __init__(self, temp_c=None, desc="", fetched_at=0.0):
        self.temp_c = temp_c
        self.desc = desc
        self.fetched_at = fetched_at

    def is_fresh(self):
        return self.temp_c is not None


class SystemInfo:
    __slots__ = (
        "cpu", "ram", "battery_pct", "battery_plugged", "battery_secs",
        "net_up_bps", "net_down_bps",
    )

    def __init__(self):
        self.cpu = 0
        self.ram = 0
        self.battery_pct = None
        self.battery_plugged = None
        self.battery_secs = None        # seconds remaining if known
        self.net_up_bps = 0.0
        self.net_down_bps = 0.0


class AudioInfo:
    """Snapshot of system master volume + mute."""
    __slots__ = ("volume", "muted", "available")

    def __init__(self, volume=None, muted=None, available=False):
        self.volume = volume   # 0.0 - 1.0 or None if not readable
        self.muted = muted     # bool or None
        self.available = available  # whether we can READ + SET volume


class Notification:
    __slots__ = ("kind", "text", "accent", "expires_at", "data")

    def __init__(self, kind, text, accent, duration_s=NOTIF_DURATION_S, data=None):
        self.kind = kind
        self.text = text
        self.accent = accent
        self.expires_at = time.time() + duration_s
        self.data = data or {}

    def alive(self):
        return time.time() < self.expires_at


class FileItem:
    """A file the user dropped onto the island."""
    __slots__ = ("path", "name", "added_at")

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path) or path
        self.added_at = time.time()

    def exists(self):
        try:
            return os.path.exists(self.path)
        except Exception:
            return False

    def size_str(self):
        try:
            n = os.path.getsize(self.path)
        except Exception:
            return ""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return ""


# ─────────────────────────────────────────────────────────────────────
#   Stopwatch
# ─────────────────────────────────────────────────────────────────────
class Stopwatch:
    def __init__(self):
        self.running = False
        self.start_t = 0.0
        self.elapsed = 0.0

    def start(self):
        if not self.running:
            self.start_t = time.time()
            self.running = True

    def pause(self):
        if self.running:
            self.elapsed += time.time() - self.start_t
            self.running = False

    def toggle(self):
        if self.running:
            self.pause()
        else:
            self.start()

    def reset(self):
        self.running = False
        self.elapsed = 0.0

    def total(self):
        if self.running:
            return self.elapsed + (time.time() - self.start_t)
        return self.elapsed

    def is_active(self):
        return self.running or self.elapsed > 0


# ─────────────────────────────────────────────────────────────────────
#   Notes  (persisted to ~/.dynamic_island_notes.json)
# ─────────────────────────────────────────────────────────────────────
def load_notes():
    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_notes(notes):
    try:
        with open(NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(notes[:NOTES_MAX], f, indent=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
#   Calendar events  (persisted to ~/.dynamic_island_events.json)
#   Each event = {"date": "YYYY-MM-DD", "time": "HH:MM" | "", "text": "...",
#                 "color": "amber" | "blue" | ...}
# ─────────────────────────────────────────────────────────────────────
def load_events():
    try:
        with open(EVENTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            # Keep only well-formed entries.
            return [e for e in data
                    if isinstance(e, dict)
                    and isinstance(e.get("date"), str)
                    and isinstance(e.get("text"), str)]
    except Exception:
        pass
    return []


def save_events(events):
    try:
        with open(EVENTS_PATH, "w", encoding="utf-8") as f:
            json.dump(events[:EVENTS_MAX], f, indent=2)
    except Exception:
        pass


def upcoming_events(events, n=EVENTS_UPCOMING):
    """Return events sorted ascending by date+time, today and onward."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_time = datetime.now().strftime("%H:%M")
    def keyfor(e):
        return (e["date"], e.get("time", "") or "00:00")
    # Keep events from today onward; if today's event time has already passed
    # we still keep it (for end-of-day glance) — but only for the current day.
    future = []
    for e in events:
        if e["date"] > today_str:
            future.append(e)
        elif e["date"] == today_str:
            future.append(e)
    future.sort(key=keyfor)
    return future[:n]


def events_on_date(events, date_str):
    """All events on a specific YYYY-MM-DD, sorted by time."""
    out = [e for e in events if e.get("date") == date_str]
    out.sort(key=lambda e: e.get("time") or "00:00")
    return out


# ─────────────────────────────────────────────────────────────────────
#   Platform-specific media: read + control
# ─────────────────────────────────────────────────────────────────────
def _media_windows():
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Mgr,
        )
        from winsdk.windows.storage.streams import (
            Buffer, DataReader, InputStreamOptions,
        )

        async def _go():
            mgr = await Mgr.request_async()
            sess = mgr.get_current_session()
            if sess is None:
                return MediaInfo()
            try:
                props = await sess.try_get_media_properties_async()
            except Exception:
                return MediaInfo()
            playback = sess.get_playback_info()
            is_playing = int(playback.playback_status) == 4

            position_s = duration_s = 0.0
            position_t = time.time()
            try:
                tl = sess.get_timeline_properties()
                start_s = tl.start_time.total_seconds()
                end_s = tl.end_time.total_seconds()
                pos_s = tl.position.total_seconds()
                duration_s = max(0.0, end_s - start_s)
                position_s = max(0.0, pos_s - start_s)
            except Exception:
                pass

            art = None
            try:
                if props.thumbnail is not None:
                    stream = await props.thumbnail.open_read_async()
                    size = stream.size
                    if size:
                        buf = Buffer(size)
                        await stream.read_async(
                            buf, size, InputStreamOptions.READ_AHEAD
                        )
                        reader = DataReader.from_buffer(buf)
                        data = bytearray(size)
                        reader.read_bytes(data)
                        img = QImage()
                        if img.loadFromData(bytes(data)):
                            art = QPixmap.fromImage(img)
            except Exception:
                pass

            return MediaInfo(
                title=props.title or "",
                artist=props.artist or "",
                is_playing=is_playing, art=art,
                position_s=position_s, duration_s=duration_s,
                position_t=position_t,
            )

        return asyncio.run(_go())
    except Exception:
        return MediaInfo()


def _media_macos():
    for app in ("Spotify", "Music"):
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 f'if application "{app}" is running then '
                 f'tell application "{app}" to '
                 f'return (player state as string) & "|" & '
                 f"(name of current track) & \"|\" & "
                 f"(artist of current track) & \"|\" & "
                 f"(player position as string) & \"|\" & "
                 f"(duration of current track as string)"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split("|")
                if len(parts) >= 3:
                    state, title, artist = parts[:3]
                    pos = float(parts[3]) if len(parts) > 3 and parts[3] else 0.0
                    dur = float(parts[4]) if len(parts) > 4 and parts[4] else 0.0
                    return MediaInfo(
                        title=title, artist=artist,
                        is_playing=state.lower() == "playing",
                        position_s=pos, duration_s=dur,
                        position_t=time.time(),
                    )
        except Exception:
            continue
    return MediaInfo()


def _media_linux():
    try:
        r = subprocess.run(
            ["playerctl", "metadata", "--format",
             "{{status}}|{{title}}|{{artist}}|{{position}}|{{mpris:length}}"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split("|")
            state, title, artist = parts[0], parts[1], parts[2]
            pos_us = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            dur_us = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            return MediaInfo(
                title=title, artist=artist,
                is_playing=state.lower() == "playing",
                position_s=pos_us / 1_000_000,
                duration_s=dur_us / 1_000_000,
                position_t=time.time(),
            )
    except Exception:
        pass
    return MediaInfo()


def get_media():
    if SYSTEM == "Windows":
        return _media_windows()
    if SYSTEM == "Darwin":
        return _media_macos()
    return _media_linux()


def media_send(cmd):
    """cmd ∈ {'play_pause', 'next', 'prev'}."""
    try:
        if SYSTEM == "Windows":
            from winsdk.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as Mgr,
            )

            async def _go():
                mgr = await Mgr.request_async()
                sess = mgr.get_current_session()
                if sess is None:
                    return
                if cmd == "play_pause":
                    info = sess.get_playback_info()
                    if int(info.playback_status) == 4:
                        await sess.try_pause_async()
                    else:
                        await sess.try_play_async()
                elif cmd == "next":
                    await sess.try_skip_next_async()
                elif cmd == "prev":
                    await sess.try_skip_previous_async()

            asyncio.run(_go())

        elif SYSTEM == "Darwin":
            for app in ("Spotify", "Music"):
                m = {"play_pause": "playpause",
                     "next": "next track",
                     "prev": "previous track"}
                subprocess.run(
                    ["osascript", "-e",
                     f'if application "{app}" is running then '
                     f'tell application "{app}" to {m[cmd]}'],
                    capture_output=True, timeout=2,
                )
        else:
            m = {"play_pause": "play-pause", "next": "next", "prev": "previous"}
            subprocess.run(["playerctl", m[cmd]], capture_output=True, timeout=2)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
#   Audio (volume + mute)  —  cross-platform
# ─────────────────────────────────────────────────────────────────────
class AudioController:
    """Read & set master output volume & mute, on every supported OS.

    Backends:
        Windows  : pycaw (read+set+mute) + keybd_event (fallback nudge keys)
        macOS    : osascript
        Linux    : pactl  →  amixer  (whichever is installed)
    """

    def __init__(self):
        self.platform = SYSTEM
        self._win_vol = None         # pycaw IAudioEndpointVolume
        self._linux_backend = None   # 'pactl' or 'amixer'
        self._available = False
        self._init_backend()

    # ── Backend init ─────────────────────────────────────────
    def _init_backend(self):
        if self.platform == "Windows":
            # We need a raw POINTER(IAudioEndpointVolume) so the comtypes
            # bindings expose the matching argspecs for both Set and Get
            # methods (the high-level `EndpointVolume` wrapper from newer
            # pycaw can fail silently on Set calls because the wrapper's
            # method signature differs from the raw COM interface).
            try:
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import (
                    AudioUtilities, IAudioEndpointVolume,
                )
                speakers = AudioUtilities.GetSpeakers()

                # Find an object that has .Activate (i.e. an IMMDevice).
                # New pycaw wraps it; old pycaw returns the IMMDevice directly.
                immdev = None
                if hasattr(speakers, "Activate"):
                    immdev = speakers
                else:
                    immdev = (getattr(speakers, "_dev", None)
                              or getattr(speakers, "dev", None)
                              or getattr(speakers, "_device", None))

                vol = None
                if immdev is not None and hasattr(immdev, "Activate"):
                    interface = immdev.Activate(
                        IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    vol = cast(interface, POINTER(IAudioEndpointVolume))

                # Last resort: take whatever .EndpointVolume returns.
                if vol is None and hasattr(speakers, "EndpointVolume"):
                    try:
                        vol = speakers.EndpointVolume
                    except Exception as e:
                        print(f"[audio] EndpointVolume property failed: {e!r}")

                if vol is None:
                    raise RuntimeError(
                        f"pycaw GetSpeakers returned unexpected type "
                        f"{type(speakers).__name__}; "
                        f"no IMMDevice and no EndpointVolume")

                # Sanity-check both reads AND a write — if the write throws,
                # we'll fall back to OS keys instead of pretending it works.
                cur = float(vol.GetMasterVolumeLevelScalar())
                _ = vol.GetMute()
                vol.SetMasterVolumeLevelScalar(cur, None)  # no-op write
                self._win_vol = vol
                self._available = True
                print(f"[audio] pycaw ready  "
                      f"(speakers={type(speakers).__name__}, "
                      f"vol={type(vol).__name__})")
            except Exception as e:
                print(f"[audio] pycaw init failed: {e!r}")
                print("[audio] +/-/mute will still work via OS keys,"
                      " but the volume bar can't be displayed.")
                self._available = False

        elif self.platform == "Darwin":
            self._available = True   # osascript is always available on macOS

        else:  # Linux
            for cmd in ("pactl", "amixer"):
                if self._has(cmd):
                    self._linux_backend = cmd
                    self._available = True
                    break

        # ── Peak meter (Windows only, optional) ───────────
        # Two paths:
        #   1. The default render device's IAudioMeterInformation. Cheap,
        #      one COM call per sample. *Should* aggregate everything but
        #      Spotify and a few other apps don't always feed it correctly.
        #   2. Per-session meters via IAudioSessionManager / GetAllSessions.
        #      Each playing app exposes its own session-level peak. We take
        #      the max across all of them — that always catches Spotify.
        # We try (1) first per tick, and if it returns 0 while we know media
        # is playing we silently switch to (2) for the rest of the run.
        self._meter = None              # device-level meter
        self._session_meters = []       # list of session-level meters
        self._sessions_refreshed_at = 0.0  # for periodic refresh
        if self.platform == "Windows":
            try:
                from ctypes import POINTER, cast
                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import AudioUtilities
                from pycaw.api.endpointvolume import IAudioMeterInformation
                spk = AudioUtilities.GetSpeakers()
                immdev = (spk if hasattr(spk, "Activate") else
                          getattr(spk, "_dev", None) or
                          getattr(spk, "dev", None))
                if immdev is not None and hasattr(immdev, "Activate"):
                    iface = immdev.Activate(
                        IAudioMeterInformation._iid_, CLSCTX_ALL, None)
                    self._meter = cast(iface, POINTER(IAudioMeterInformation))
                    _ = self._meter.GetPeakValue()
                # Also build session meters up-front so Spotify works
                # immediately on first tick.
                self._refresh_session_meters()
                print(f"[audio] peak meter ready  "
                      f"(device={self._meter is not None}, "
                      f"sessions={len(self._session_meters)})")
            except Exception as e:
                print(f"[audio] peak meter unavailable: {e!r}")
                self._meter = None
                self._session_meters = []

    def _refresh_session_meters(self):
        """Rebuild the list of per-session peak meters. New sessions appear
        when an app starts playing audio, so we re-fetch periodically."""
        if self.platform != "Windows":
            return
        try:
            from ctypes import POINTER, cast
            from pycaw.pycaw import AudioUtilities
            from pycaw.api.endpointvolume import IAudioMeterInformation
            sessions = AudioUtilities.GetAllSessions()
            meters = []
            for s in sessions:
                # Each session has a control2 interface that can be queried
                # for IAudioMeterInformation via QueryInterface.
                try:
                    ctl = s._ctl   # ISimpleAudioVolume's parent control
                    meter = ctl.QueryInterface(IAudioMeterInformation)
                    meters.append(meter)
                except Exception:
                    # Some sessions (system sounds, etc.) don't expose the
                    # interface. Skip them silently.
                    continue
            self._session_meters = meters
            self._sessions_refreshed_at = time.time()
        except Exception as e:
            # Don't spam console — print only the first failure.
            if not getattr(self, "_session_refresh_warned", False):
                print(f"[audio] session meter refresh failed: {e!r}")
                self._session_refresh_warned = True

    def get_peak(self):
        """Return current output peak 0.0..1.0, or None if no backend.
        Tries device-level first; falls back to max across session meters
        (which catches Spotify even when device meter reports 0)."""
        peak = 0.0
        got_anything = False

        # Path 1: device-level
        try:
            if self._meter is not None:
                peak = max(peak, float(self._meter.GetPeakValue()))
                got_anything = True
        except Exception:
            pass

        # Path 2: per-session, max across all
        try:
            for m in self._session_meters:
                try:
                    peak = max(peak, float(m.GetPeakValue()))
                    got_anything = True
                except Exception:
                    pass
        except Exception:
            pass

        # Refresh session list every 3s — apps come and go.
        now = time.time()
        if now - self._sessions_refreshed_at > 3.0:
            self._refresh_session_meters()

        return peak if got_anything else None

    @staticmethod
    def _has(cmd):
        try:
            return subprocess.run(
                ["which", cmd], capture_output=True, timeout=1,
            ).returncode == 0
        except Exception:
            return False

    @property
    def available(self):
        return self._available

    # ── Read ─────────────────────────────────────────────────
    def get(self):
        """Return AudioInfo snapshot."""
        info = AudioInfo(available=self._available)
        if not self._available:
            return info
        try:
            if self.platform == "Windows":
                info.volume = float(self._win_vol.GetMasterVolumeLevelScalar())
                info.muted = bool(self._win_vol.GetMute())
            elif self.platform == "Darwin":
                r = subprocess.run(
                    ["osascript", "-e",
                     'set v to output volume of (get volume settings)\n'
                     'set m to output muted of (get volume settings)\n'
                     'return (v as string) & "|" & (m as string)'],
                    capture_output=True, text=True, timeout=2,
                )
                if r.returncode == 0 and r.stdout.strip():
                    parts = r.stdout.strip().split("|")
                    if len(parts) == 2:
                        try:
                            info.volume = max(0.0, min(1.0, int(parts[0]) / 100.0))
                        except ValueError:
                            pass
                        info.muted = parts[1].strip().lower() == "true"
            else:  # Linux
                if self._linux_backend == "pactl":
                    r = subprocess.run(
                        ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                        capture_output=True, text=True, timeout=2,
                    )
                    if r.returncode == 0:
                        # "Volume: front-left: 32768 / 50% / -18.06 dB,  ..."
                        for tok in r.stdout.split():
                            if tok.endswith("%"):
                                try:
                                    info.volume = int(tok[:-1]) / 100.0
                                    break
                                except ValueError:
                                    pass
                    r2 = subprocess.run(
                        ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                        capture_output=True, text=True, timeout=2,
                    )
                    if r2.returncode == 0:
                        info.muted = "yes" in r2.stdout.lower()
                else:  # amixer
                    r = subprocess.run(
                        ["amixer", "get", "Master"],
                        capture_output=True, text=True, timeout=2,
                    )
                    if r.returncode == 0:
                        out = r.stdout
                        # Find first xx% in output
                        i = out.find("[")
                        while i != -1:
                            j = out.find("%", i)
                            if j > 0 and out[i + 1:j].isdigit():
                                info.volume = int(out[i + 1:j]) / 100.0
                                break
                            i = out.find("[", j)
                        info.muted = "[off]" in out
        except Exception:
            pass
        return info

    # ── Write ────────────────────────────────────────────────
    def set_volume(self, frac):
        frac = max(0.0, min(1.0, float(frac)))
        try:
            if self.platform == "Windows" and self._win_vol is not None:
                self._win_vol.SetMasterVolumeLevelScalar(frac, None)
            elif self.platform == "Darwin":
                pct = int(round(frac * 100))
                subprocess.run(
                    ["osascript", "-e", f"set volume output volume {pct}"],
                    capture_output=True, timeout=2,
                )
            elif self._linux_backend == "pactl":
                pct = int(round(frac * 100))
                subprocess.run(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                    capture_output=True, timeout=2,
                )
            elif self._linux_backend == "amixer":
                pct = int(round(frac * 100))
                subprocess.run(
                    ["amixer", "-q", "set", "Master", f"{pct}%"],
                    capture_output=True, timeout=2,
                )
        except Exception as e:
            print(f"[audio] set_volume({frac:.3f}) failed: {e!r}")

    def set_mute(self, mute):
        try:
            if self.platform == "Windows" and self._win_vol is not None:
                self._win_vol.SetMute(1 if mute else 0, None)
            elif self.platform == "Darwin":
                subprocess.run(
                    ["osascript", "-e",
                     f'set volume output muted {"true" if mute else "false"}'],
                    capture_output=True, timeout=2,
                )
            elif self._linux_backend == "pactl":
                subprocess.run(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@",
                     "1" if mute else "0"],
                    capture_output=True, timeout=2,
                )
            elif self._linux_backend == "amixer":
                subprocess.run(
                    ["amixer", "-q", "set", "Master",
                     "mute" if mute else "unmute"],
                    capture_output=True, timeout=2,
                )
        except Exception as e:
            print(f"[audio] set_mute({mute}) failed: {e!r}")

    def toggle_mute(self):
        info = self.get()
        if info.muted is None:
            # No backend that can read — try a generic toggle key
            self._send_mute_key()
        else:
            self.set_mute(not info.muted)

    def step(self, delta):
        """delta is signed fraction; e.g. +0.05 for +5%."""
        info = self.get()
        if info.volume is not None:
            self.set_volume(info.volume + delta)
        else:
            # No read backend → use OS key for nudge
            if delta > 0:
                self._send_volume_key(up=True)
            else:
                self._send_volume_key(up=False)

    # ── OS-key fallbacks (Windows w/o pycaw) ─────────────────
    @staticmethod
    def _send_volume_key(up=True):
        if SYSTEM != "Windows":
            return
        try:
            import ctypes
            VK = 0xAF if up else 0xAE
            ctypes.windll.user32.keybd_event(VK, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK, 0, 2, 0)
        except Exception:
            pass

    @staticmethod
    def _send_mute_key():
        if SYSTEM != "Windows":
            return
        try:
            import ctypes
            VK_MUTE = 0xAD
            ctypes.windll.user32.keybd_event(VK_MUTE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(VK_MUTE, 0, 2, 0)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
#   Weather
# ─────────────────────────────────────────────────────────────────────
def fetch_weather():
    try:
        req = urllib.request.Request(
            "https://wttr.in/?format=j1",
            headers={"User-Agent": "DynamicIslandLaptop/4.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        cur = data["current_condition"][0]
        return WeatherInfo(
            temp_c=int(cur["temp_C"]),
            desc=cur["weatherDesc"][0]["value"],
            fetched_at=time.time(),
        )
    except Exception:
        return WeatherInfo()


# ─────────────────────────────────────────────────────────────────────
#   Worker threads
# ─────────────────────────────────────────────────────────────────────
class MediaPoller(QThread):
    updated = pyqtSignal(object)

    def run(self):
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass
        while not self.isInterruptionRequested():
            self.updated.emit(get_media())
            self.msleep(MEDIA_POLL_MS)


class SystemPoller(QThread):
    """Polls CPU / RAM / battery / network. Calculates net throughput."""
    updated = pyqtSignal(object)

    def run(self):
        prev_up = prev_down = 0
        prev_t = 0.0
        while not self.isInterruptionRequested():
            info = SystemInfo()
            try:
                info.cpu = int(psutil.cpu_percent(interval=None))
                info.ram = int(psutil.virtual_memory().percent)
            except Exception:
                pass
            try:
                b = psutil.sensors_battery()
                if b is not None:
                    info.battery_pct = int(b.percent)
                    info.battery_plugged = bool(b.power_plugged)
                    if b.secsleft not in (
                        getattr(psutil, "POWER_TIME_UNLIMITED", -1),
                        getattr(psutil, "POWER_TIME_UNKNOWN", -2),
                        -1, -2,
                    ) and b.secsleft > 0:
                        info.battery_secs = int(b.secsleft)
            except Exception:
                pass
            try:
                c = psutil.net_io_counters()
                now = time.time()
                if prev_t > 0:
                    dt = max(0.001, now - prev_t)
                    info.net_up_bps = max(0.0, (c.bytes_sent - prev_up) / dt)
                    info.net_down_bps = max(0.0, (c.bytes_recv - prev_down) / dt)
                prev_up = c.bytes_sent
                prev_down = c.bytes_recv
                prev_t = now
            except Exception:
                pass
            self.updated.emit(info)
            self.msleep(SYSTEM_POLL_MS)


class WeatherPoller(QThread):
    updated = pyqtSignal(object)

    def run(self):
        while not self.isInterruptionRequested():
            info = fetch_weather()
            if info.is_fresh():
                self.updated.emit(info)
            for _ in range(WEATHER_REFRESH_S):
                if self.isInterruptionRequested():
                    return
                self.msleep(1000)


class AudioPoller(QThread):
    """Polls master volume + mute. Emits whenever they change."""
    updated = pyqtSignal(object)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        last_vol = None
        last_mute = None
        while not self.isInterruptionRequested():
            info = self.controller.get()
            self.updated.emit(info)
            last_vol = info.volume
            last_mute = info.muted
            _ = last_vol, last_mute  # quiet linter
            self.msleep(AUDIO_POLL_MS)


# ─────────────────────────────────────────────────────────────────────
#   Fullscreen detection (Windows)
# ─────────────────────────────────────────────────────────────────────
def is_fullscreen_active():
    """True only for genuinely fullscreen apps (videos, games), NOT maximised
    windows. A real fullscreen window has no title bar (no WS_CAPTION style)."""
    if SYSTEM != "Windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        cls = (ctypes.c_wchar * 256)()
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value in ("WorkerW", "Progman", "Shell_TrayWnd"):
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        is_full_size = (rect.right - rect.left) >= sw and (rect.bottom - rect.top) >= sh
        if not is_full_size:
            return False
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        style = get_style(hwnd, GWL_STYLE)
        has_caption_or_frame = bool(style & (WS_CAPTION | WS_THICKFRAME))
        return not has_caption_or_frame
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
#   Hotkey signal bridge
# ─────────────────────────────────────────────────────────────────────
class HotkeyBridge(QObject):
    triggered = pyqtSignal()


# ─────────────────────────────────────────────────────────────────────
#   THE ISLAND
# ─────────────────────────────────────────────────────────────────────
class DynamicIsland(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        # ── Settings (theme + alerts pref) ─────────────────────
        self.settings = load_settings()
        theme_name = self.settings.get("theme", "classic")
        if theme_name not in THEMES:
            theme_name = "classic"
        self.theme = THEMES[theme_name]

        # ── Core state ──────────────────────────────────────────
        self.state = State.HIDDEN
        self.card = Card.AUTO
        self.media = MediaInfo()
        self.weather = WeatherInfo()
        self.sysinfo = SystemInfo()
        self.audio = AudioInfo()
        self.timer_end = None
        self.stopwatch = Stopwatch()
        self.notif = None
        self.notes = load_notes()
        self.events = load_events()

        # Audio visualiser: ring buffer of recent peak samples, fed by _tick.
        # We keep two parallel buffers:
        #   _peak_history    — smoothed (VU-meter style, slower attack/decay)
        #   _peak_history_raw — instantaneous (faster, more reactive bars)
        # Visualizers sample from raw so chorus variance survives; the
        # envelope tracker uses the smoothed view for stability.
        self._peak_history = [0.0] * 32
        self._peak_history_raw = [0.0] * 32
        self._peak_idx = 0
        self._peak_cur = 0.0
        # Adaptive gain: tracks the recent loudness ceiling so we can normalise.
        # Without this, scaling peaks by a fixed factor saturates on big drops.
        # With it, the bars centre around 50–70% even on a loud song and use
        # the full vertical range during transients (kicks, drops, etc).
        self._peak_envelope = 0.15  # running max — recovers slowly
        # Network throughput history for sparkline on System card.
        self._net_down_history = [0.0] * 24
        self._net_up_history = [0.0] * 24
        self._net_hist_idx = 0
        self.files = []   # List[FileItem]

        # ── Toggles & mode flags ────────────────────────────────
        self.pinned = False
        self.alerts_muted = bool(self.settings.get("alerts_muted", False))
        self.disabled = bool(self.settings.get("disabled", False))

        # ── User-tunable preferences (saved to settings) ────────
        # All of these can be edited from the Settings card.
        self.hide_on_fullscreen = bool(self.settings.get(
            "hide_on_fullscreen", True))
        self.low_battery_pct = int(self.settings.get(
            "low_battery_pct", LOW_BATTERY_PCT))
        self.hotzone_w = int(self.settings.get(
            "hotzone_w", HOTZONE_W))
        # Accent colour names — resolve against the active theme so they
        # follow theme changes naturally. Valid names are the ones in
        # ACCENT_NAMES below.
        self.viz_accent_name = self.settings.get("viz_accent", "green")
        self.viz_peak_accent_name = self.settings.get("viz_peak_accent", "amber")
        self.progress_accent_name = self.settings.get("progress_accent", "green")

        # ── Ephemeral / detection state ─────────────────────────
        self._fullscreen = False
        self.hovering = False
        self._buttons = []
        self._drag_zones = []   # [(rect, mime_factory)] — areas that start a drag
        self._hover_pos = None  # last known cursor position over widget
        self._hovered_idx = -1  # index in _buttons currently under cursor
        self._prev_battery_plugged = None
        self._prev_low_battery = False
        self._prev_audio_volume = None
        self._prev_audio_muted = None
        self._first_audio_seen = False
        self._manual_summon_until = 0.0
        self._last_clip = ""
        self._scroll_accum = 0
        self._drag_hover = False              # external drag entered
        self._press_drag = None               # (start_qpoint, mime_factory)
        self._volume_track_rect = None        # set during paintEvent
        self._settings_page = 0               # 0 = general, 1 = colours
        # Calendar: which month is currently shown in the grid (defaults to
        # actual current month; user can flip back/forward).
        _now = datetime.now()
        self._cal_year = _now.year
        self._cal_month = _now.month
        # Date the user clicked on the grid — None = no selection
        self._cal_selected = None  # ISO YYYY-MM-DD string

        scr = QGuiApplication.primaryScreen().geometry()
        self.screen_w = scr.width()
        self.screen_y = scr.y()

        # Start fully hidden. The widget only becomes visible when the user
        # hovers the top-middle edge or something pillable happens.
        self.setGeometry(self._geom_for(State.HIDDEN))
        # Note: NOT calling self.show() here. _set_state will call show()
        # when it transitions to PILL or EXPAND.

        # ── Audio controller (created up-front, polled in worker) ──
        self.audio_ctrl = AudioController()

        # ── Timers ──────────────────────────────────────────────
        self.hover_timer = QTimer(self)
        self.hover_timer.timeout.connect(self._poll_hover)
        self.hover_timer.start(HOVER_POLL_MS)

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._tick)
        self.tick_timer.start(33)

        self.fs_timer = QTimer(self)
        self.fs_timer.timeout.connect(self._poll_fullscreen)
        self.fs_timer.start(FULLSCREEN_POLL_MS)

        self.topmost_timer = QTimer(self)
        self.topmost_timer.timeout.connect(self._force_topmost)
        self.topmost_timer.start(3000)

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._collapse_to_resting)

        # ── Animation ───────────────────────────────────────────
        self.anim = QPropertyAnimation(self, b"geometry")
        self.anim.setDuration(ANIM_MS)
        self.anim.setEasingCurve(QEasingCurve.Type.OutBack)

        # ── Workers ─────────────────────────────────────────────
        self.media_poller = MediaPoller()
        self.media_poller.updated.connect(self._on_media)
        self.media_poller.start()

        self.sys_poller = SystemPoller()
        self.sys_poller.updated.connect(self._on_sys)
        self.sys_poller.start()

        self.weather_poller = WeatherPoller()
        self.weather_poller.updated.connect(self._on_weather)
        self.weather_poller.start()

        self.audio_poller = AudioPoller(self.audio_ctrl)
        self.audio_poller.updated.connect(self._on_audio)
        self.audio_poller.start()

        # ── Clipboard watcher ───────────────────────────────────
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._on_clipboard_change)
        self._clipboard = clipboard

    # ────────────────────────────────────────────────────────────
    #   Geometry / state
    # ────────────────────────────────────────────────────────────
    def _geom_for(self, state):
        if state == State.HIDDEN:
            w, h = HIDDEN_W, HIDDEN_H
        elif state == State.PILL:
            w, h = PILL_W, PILL_H
        else:
            w, h = EXPAND_W, EXPAND_H
        x = self.screen_w // 2 - w // 2
        y = self.screen_y + (TOP_MARGIN if state != State.HIDDEN else 0)
        return QRect(x, y, w, h)

    def _animate_to(self, geom):
        self.anim.stop()
        self.anim.setStartValue(self.geometry())
        self.anim.setEndValue(geom)
        self.anim.start()

    def _set_state(self, state):
        # Hard chokepoint: when the user has hidden the island from the tray,
        # nothing on earth can make it visible until they re-enable it.
        if self.disabled:
            state = State.HIDDEN
        if state == self.state:
            return
        self.state = state
        if state == State.HIDDEN:
            # Fully hide the window — no sliver, no hairline, no anything.
            # Hover detection still works because _poll_hover uses the system
            # cursor position, not widget events.
            self.anim.stop()
            self.setGeometry(self._geom_for(state))
            self.hide()
        else:
            # Make sure we're shown BEFORE animating in. show() is a no-op
            # if already visible.
            if not self.isVisible():
                # Start the geometry collapsed, then animate to target so
                # the widget appears to slide down from the bezel.
                start = self._geom_for(State.HIDDEN)
                self.setGeometry(start)
                self.show()
            self._animate_to(self._geom_for(state))
            self.raise_()
            self._force_topmost()
        self.update()

    def _force_topmost(self):
        if SYSTEM != "Windows":
            return
        if self.state == State.HIDDEN:
            return
        try:
            import ctypes
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(
                int(self.winId()), HWND_TOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            pass

    # ────────────────────────────────────────────────────────────
    #   Tick — animations, expirations, sound
    # ────────────────────────────────────────────────────────────
    def _tick(self):
        if self.timer_end is not None and time.time() >= self.timer_end:
            self.timer_end = None
            self._notify("timer", "Timer finished", self.theme.amber, duration_s=6)
            self._beep()

        if self.notif is not None and not self.notif.alive():
            self.notif = None
            self._collapse_to_resting()

        # Feed the visualizer's ring buffer with a real peak sample if we
        # can. Only bother when we're actually visible — saves COM calls
        # while idle/hidden. Without a meter (or when paused), decay toward
        # zero so the bars settle to flat.
        if self.state != State.HIDDEN:
            peak = (self.audio_ctrl.get_peak()
                    if hasattr(self, "audio_ctrl") else None)
            if peak is None or not self.media.is_playing:
                # Smooth decay; avoids a hard cut to flat when pausing.
                self._peak_cur *= 0.86
                # Let the envelope decay slowly too so it's ready when
                # play resumes.
                self._peak_envelope *= 0.997
            else:
                # Fast attack, slow release — like a real VU meter.
                if peak > self._peak_cur:
                    self._peak_cur = self._peak_cur * 0.4 + peak * 0.6
                else:
                    self._peak_cur = self._peak_cur * 0.82 + peak * 0.18
                # Adaptive envelope: instant rise to new max, slow decay.
                # This is what lets us auto-gain to whatever's loud right
                # now, so big beats don't saturate the bars.
                if peak > self._peak_envelope:
                    self._peak_envelope = peak
                else:
                    # Decay over ~3-5 seconds. At 30 fps tick that's ~100-150
                    # samples; coefficient 0.992 gives ~5s half-life.
                    self._peak_envelope = max(
                        0.05,
                        self._peak_envelope * 0.992 + peak * 0.008
                    )
            self._peak_history[self._peak_idx] = self._peak_cur
            # Raw history: just the unsmoothed sample, clamped to envelope+headroom
            # so a freak spike doesn't blow out the visualiser.
            raw_val = max(0.0, peak if (peak is not None and self.media.is_playing) else 0.0)
            self._peak_history_raw[self._peak_idx] = raw_val
            self._peak_idx = (self._peak_idx + 1) % len(self._peak_history)

        self.update()

    def _beep(self):
        if not self.alerts_muted:
            try:
                QApplication.beep()
            except Exception:
                pass

    # ────────────────────────────────────────────────────────────
    #   Hover detection
    # ────────────────────────────────────────────────────────────
    def _poll_hover(self):
        if self.pinned:
            return
        if self._fullscreen and time.time() > self._manual_summon_until:
            return
        pos = QCursor.pos()
        cx = self.screen_w // 2
        in_x = abs(pos.x() - cx) <= self.hotzone_w // 2
        live_h = max(HOTZONE_H, self.height() + TOP_MARGIN + 2)
        in_y = pos.y() <= self.screen_y + live_h
        in_zone = in_x and in_y
        if self.geometry().contains(pos):
            in_zone = True

        if in_zone == self.hovering:
            return
        self.hovering = in_zone
        if in_zone:
            self.hide_timer.stop()
            self._set_state(State.EXPAND)
        else:
            self.hide_timer.start(HIDE_DELAY_MS)

    def _collapse_to_resting(self):
        if self.hovering or self.pinned or self._drag_hover:
            return
        if time.time() < self._manual_summon_until:
            return
        if self._anything_pillable():
            self._set_state(State.PILL)
        else:
            self._set_state(State.HIDDEN)
        self.card = Card.AUTO

    def _anything_pillable(self):
        return (
            self._notif_alive()
            or self._timer_active()
            or self.media.is_active()
            or len(self.files) > 0
        )

    # ────────────────────────────────────────────────────────────
    #   Fullscreen
    # ────────────────────────────────────────────────────────────
    def _poll_fullscreen(self):
        if self.pinned:
            return
        if not self.hide_on_fullscreen:
            # User opted out — don't bother with detection at all.
            self._fullscreen = False
            return
        was_fs = self._fullscreen
        self._fullscreen = is_fullscreen_active()
        if self._fullscreen and not was_fs:
            self._set_state(State.HIDDEN)
        elif not self._fullscreen and was_fs:
            self._collapse_to_resting()

    # ────────────────────────────────────────────────────────────
    #   Worker callbacks
    # ────────────────────────────────────────────────────────────
    def _on_media(self, info):
        was_active = self.media.is_active()
        self.media = info
        now_active = info.is_active()
        if self.hovering or self._timer_active() or self.pinned:
            return
        if now_active and not was_active:
            self._set_state(State.PILL)
        elif was_active and not now_active and not self._anything_pillable():
            self._set_state(State.HIDDEN)

    def _on_sys(self, info):
        if (self._prev_battery_plugged is not None
                and info.battery_plugged is not None
                and info.battery_plugged != self._prev_battery_plugged):
            if info.battery_plugged:
                self._notify("charge", f"Charging  · {info.battery_pct}%",
                             self.theme.green)
            else:
                self._notify("unplug", f"On battery  · {info.battery_pct}%",
                             self.theme.text_dim)
        is_low = (info.battery_pct is not None
                  and info.battery_pct <= self.low_battery_pct
                  and not info.battery_plugged)
        if is_low and not self._prev_low_battery:
            self._notify("low_batt", f"Battery low  · {info.battery_pct}%",
                         self.theme.red, duration_s=6)
            self._beep()
        self._prev_low_battery = is_low
        self._prev_battery_plugged = info.battery_plugged
        self.sysinfo = info
        # Net history for sparkline.
        self._net_down_history[self._net_hist_idx] = info.net_down_bps
        self._net_up_history[self._net_hist_idx] = info.net_up_bps
        self._net_hist_idx = (self._net_hist_idx + 1) % len(self._net_down_history)

    def _on_weather(self, info):
        self.weather = info

    def _on_audio(self, info):
        self.audio = info
        # Skip the very first reading to avoid showing a spurious pill on boot.
        if not self._first_audio_seen:
            self._first_audio_seen = True
            self._prev_audio_volume = info.volume
            self._prev_audio_muted = info.muted
            return

        # Mute change wins over volume change (more important UX-wise).
        if (info.muted is not None
                and self._prev_audio_muted is not None
                and info.muted != self._prev_audio_muted):
            text = "Muted" if info.muted else "Unmuted"
            color = self.theme.red if info.muted else self.theme.green
            self._notify("vol", text, color,
                         duration_s=VOL_PILL_DURATION,
                         data={"volume": info.volume,
                               "muted": info.muted,
                               "is_mute_event": True})
        elif (info.volume is not None
              and self._prev_audio_volume is not None
              and abs(info.volume - self._prev_audio_volume) > 0.01):
            color = self.theme.text_dim if info.muted else self.theme.blue
            self._notify("vol",
                         f"Volume {int(round(info.volume * 100))}%",
                         color,
                         duration_s=VOL_PILL_DURATION,
                         data={"volume": info.volume,
                               "muted": info.muted or False,
                               "is_mute_event": False})

        self._prev_audio_volume = info.volume
        self._prev_audio_muted = info.muted

    # ────────────────────────────────────────────────────────────
    #   Clipboard watcher
    # ────────────────────────────────────────────────────────────
    def _on_clipboard_change(self):
        if self._clipboard is None:
            return
        md = self._clipboard.mimeData()
        if md is None or not md.hasText():
            return
        text = md.text()
        if not text or not text.strip():
            return
        if text == self._last_clip:
            return
        self._last_clip = text
        preview = text.strip().replace("\n", " ").replace("\r", " ")
        if len(preview) > CLIP_PREVIEW_LEN:
            preview = preview[:CLIP_PREVIEW_LEN] + "…"
        self._notify("clip", f"Copied  · {preview}", self.theme.purple,
                     duration_s=CLIP_PILL_DURATION)

    # ────────────────────────────────────────────────────────────
    #   Notifications
    # ────────────────────────────────────────────────────────────
    def _notify(self, kind, text, accent, duration_s=NOTIF_DURATION_S, data=None):
        # When the user has hidden the island from the tray, drop notifications
        # on the floor instead of queueing them — otherwise they'd all pop the
        # moment the island is re-enabled. Beeps still play (those are
        # independent) so timer completions are still audible.
        if self.disabled:
            return
        self.notif = Notification(kind, text, accent, duration_s, data)
        if not self.hovering and not self.pinned:
            self._set_state(State.PILL)

    def _notif_alive(self):
        return self.notif is not None and self.notif.alive()

    # ────────────────────────────────────────────────────────────
    #   Public ops — timer / pin / alerts / notes
    # ────────────────────────────────────────────────────────────
    def _timer_active(self):
        return self.timer_end is not None and time.time() < self.timer_end

    def start_timer(self, seconds):
        self.timer_end = time.time() + seconds
        if not self.hovering and not self.pinned:
            self._set_state(State.PILL)

    def cancel_timer(self):
        self.timer_end = None
        self._collapse_to_resting()

    def manual_summon(self, seconds=4):
        self._manual_summon_until = time.time() + seconds
        self._set_state(State.EXPAND)
        QTimer.singleShot(int(seconds * 1000) + 100,
                          self._maybe_collapse_after_summon)

    def _maybe_collapse_after_summon(self):
        if self.pinned or self.hovering:
            return
        pos = QCursor.pos()
        cx = self.screen_w // 2
        in_x = abs(pos.x() - cx) <= self.hotzone_w // 2
        live_h = max(HOTZONE_H, self.height() + TOP_MARGIN + 2)
        in_y = pos.y() <= self.screen_y + live_h
        in_zone = (in_x and in_y) or self.geometry().contains(pos)
        if in_zone:
            self.hovering = True
            self.hide_timer.stop()
            return
        self._collapse_to_resting()

    def toggle_pin(self):
        self.pinned = not self.pinned
        if self.pinned:
            self._set_state(State.EXPAND)
        else:
            self._collapse_to_resting()

    def toggle_alerts(self):
        self.alerts_muted = not self.alerts_muted
        self.settings["alerts_muted"] = self.alerts_muted
        save_settings(self.settings)

    # ── Hide mode (re-enable from tray only) ────────────────
    def set_disabled(self, disabled):
        was = self.disabled
        self.disabled = bool(disabled)
        self.settings["disabled"] = self.disabled
        save_settings(self.settings)
        if self.disabled:
            # Drop any pending visible state. Pinning is meaningless when
            # disabled — clear it so re-enable starts from a clean slate.
            self.notif = None
            self.pinned = False
            self.hovering = False
            self._manual_summon_until = 0.0
            # Cannot use _set_state here — it would short-circuit due to
            # state==HIDDEN already. Force a real geometry write AND hide
            # the widget so any currently-painted pill vanishes immediately.
            self.anim.stop()
            self.state = State.HIDDEN
            self.setGeometry(self._geom_for(State.HIDDEN))
            self.hide()
            self.update()
        elif was:
            # Just got re-enabled — show a small confirmation pill, then let
            # normal hover/poll machinery take over.
            self._collapse_to_resting()
            self._notify("enable", "Island enabled", self.theme.green,
                         duration_s=1.4)

    def toggle_disabled(self):
        self.set_disabled(not self.disabled)
    def add_note_dialog(self):
        text, ok = QInputDialog.getMultiLineText(
            self, "Quick Note", "Note  (Cmd/Ctrl+Enter to save):",
        )
        if ok and text and text.strip():
            self.notes.insert(0, {
                "text": text.strip(),
                "ts": time.time(),
            })
            save_notes(self.notes)
            self._notify("note", "Note saved", self.theme.green, duration_s=2)

    def clear_notes(self):
        self.notes = []
        save_notes(self.notes)

    # ── Theme ───────────────────────────────────────────────
    def set_theme(self, name):
        if name not in THEMES:
            return
        self.theme = THEMES[name]
        self.settings["theme"] = name
        save_settings(self.settings)
        self._notify("theme", f"Theme  · {self.theme.label}",
                     self.theme.purple, duration_s=1.4)
        self.update()

    # ── User-tunable preferences ────────────────────────────
    def set_low_battery_pct(self, pct):
        pct = max(5, min(50, int(pct)))
        self.low_battery_pct = pct
        self.settings["low_battery_pct"] = pct
        save_settings(self.settings)
        self.update()

    def set_hotzone_w(self, w):
        w = max(180, min(800, int(w)))
        self.hotzone_w = w
        self.settings["hotzone_w"] = w
        save_settings(self.settings)
        self.update()

    def toggle_hide_on_fullscreen(self):
        self.hide_on_fullscreen = not self.hide_on_fullscreen
        self.settings["hide_on_fullscreen"] = self.hide_on_fullscreen
        save_settings(self.settings)
        if not self.hide_on_fullscreen:
            self._fullscreen = False
        self.update()

    # ── Accent colours ──────────────────────────────────────
    def _accent(self, name, default="green"):
        """Resolve an accent name (e.g. 'green') to a QColor on the active
        theme. Falls back to 'default' if the name isn't valid."""
        if name not in ACCENT_NAMES:
            name = default
        return getattr(self.theme, name, getattr(self.theme, default))

    def viz_accent(self):
        return self._accent(self.viz_accent_name, "green")

    def viz_peak_accent(self):
        return self._accent(self.viz_peak_accent_name, "amber")

    def progress_accent(self):
        return self._accent(self.progress_accent_name, "green")

    def set_viz_accent(self, name):
        if name not in ACCENT_NAMES:
            return
        self.viz_accent_name = name
        self.settings["viz_accent"] = name
        save_settings(self.settings)
        self.update()

    def set_viz_peak_accent(self, name):
        if name not in ACCENT_NAMES:
            return
        self.viz_peak_accent_name = name
        self.settings["viz_peak_accent"] = name
        save_settings(self.settings)
        self.update()

    def set_progress_accent(self, name):
        if name not in ACCENT_NAMES:
            return
        self.progress_accent_name = name
        self.settings["progress_accent"] = name
        save_settings(self.settings)
        self.update()

    def reset_preferences(self):
        """Restore all user-tunable preferences to defaults. Doesn't touch
        notes, files, alerts mute, or the disabled flag."""
        self.set_theme("classic")
        self.low_battery_pct = LOW_BATTERY_PCT
        self.hotzone_w = HOTZONE_W
        self.hide_on_fullscreen = True
        self.viz_accent_name = "green"
        self.viz_peak_accent_name = "amber"
        self.progress_accent_name = "green"
        self.settings["low_battery_pct"] = LOW_BATTERY_PCT
        self.settings["hotzone_w"] = HOTZONE_W
        self.settings["hide_on_fullscreen"] = True
        self.settings["viz_accent"] = "green"
        self.settings["viz_peak_accent"] = "amber"
        self.settings["progress_accent"] = "green"
        save_settings(self.settings)
        self._notify("reset", "Settings reset", self.theme.amber, duration_s=1.5)
        self.update()

    # ── Audio ───────────────────────────────────────────────
    def set_volume_frac(self, frac):
        if not self.audio_ctrl.available:
            return
        self.audio_ctrl.set_volume(frac)
        # Optimistic UI update so the bar visibly moves NOW. We deliberately
        # do NOT touch _prev_audio_volume here — leave that to the poller,
        # so the volume-change pill animation still fires for our own changes
        # (otherwise the user sees no feedback that the change took effect).
        self.audio.volume = max(0.0, min(1.0, float(frac)))

    def step_volume(self, delta):
        self.audio_ctrl.step(delta)
        if self.audio.volume is not None:
            self.audio.volume = max(0.0, min(1.0, self.audio.volume + delta))

    def toggle_system_mute(self):
        self.audio_ctrl.toggle_mute()
        if self.audio.muted is not None:
            self.audio.muted = not self.audio.muted

    # ── Files ───────────────────────────────────────────────
    def add_files(self, paths):
        added = 0
        for p in paths:
            if not p:
                continue
            if any(f.path == p for f in self.files):
                continue
            self.files.insert(0, FileItem(p))
            added += 1
            if len(self.files) > FILES_MAX:
                self.files = self.files[:FILES_MAX]
        if added:
            self._notify("files",
                         f"📎  {added} file{'s' if added != 1 else ''} attached",
                         self.theme.green, duration_s=2.0)
            self.card = Card.FILES
            if not self.hovering and not self.pinned:
                self._set_state(State.PILL)

    def remove_file(self, idx):
        if 0 <= idx < len(self.files):
            del self.files[idx]
            self.update()

    def clear_files(self):
        self.files = []
        self.update()
        self._collapse_to_resting()

    # ────────────────────────────────────────────────────────────
    #   Drag-and-drop  —  external files INTO the island
    # ────────────────────────────────────────────────────────────
    def dragEnterEvent(self, ev):
        md = ev.mimeData()
        if md is not None and md.hasUrls():
            self._drag_hover = True
            self.hide_timer.stop()
            if self.state != State.EXPAND:
                self._set_state(State.EXPAND)
            self.card = Card.FILES
            ev.acceptProposedAction()
            self.update()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData() is not None and ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev):
        self._drag_hover = False
        self.update()
        self.hide_timer.start(HIDE_DELAY_MS)

    def dropEvent(self, ev):
        md = ev.mimeData()
        self._drag_hover = False
        if md is None or not md.hasUrls():
            ev.ignore()
            self.update()
            return
        paths = []
        for url in md.urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.add_files(paths)
            ev.acceptProposedAction()
        else:
            ev.ignore()
        self.update()

    # ────────────────────────────────────────────────────────────
    #   Painting — entry
    # ────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        if self.state == State.HIDDEN:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        # Pill state stays fully rounded (h/2). Expanded state gets a generous
        # 30px corner — chunkier squircle, closer to a macOS widget than a pill.
        radius = min(rect.height() / 2, 30)

        # 1. Soft drop shadow — drawn as a slightly larger, slightly offset
        #    semi-transparent rect behind the main bg. Subtle on dark themes,
        #    more visible on light themes. Helps the island feel like it's
        #    floating instead of pasted on.
        shadow = QColor(0, 0, 0, 90 if self.theme.bg.lightness() > 120 else 60)
        sp = QPainterPath()
        sp.addRoundedRect(rect.x() - 1, rect.y() + 2,
                          rect.width() + 2, rect.height() + 2,
                          radius, radius)
        p.fillPath(sp, shadow)

        # 2. Main bg with a subtle top-to-bottom gradient. Top is the
        #    theme's bg color; bottom is slightly darker. This gives the
        #    island a physical-object feel — like real glass that catches
        #    light at the top edge. Skipped on the light Mono theme.
        path = QPainterPath()
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(),
                            radius, radius)
        if self.theme.bg.lightness() < 200:
            from PyQt6.QtGui import QLinearGradient
            grad = QLinearGradient(0, rect.y(), 0, rect.bottom())
            top_col = self._tint(self.theme.bg, 0.05)
            bot_col = self._tint(self.theme.bg, -0.08)
            grad.setColorAt(0.0, top_col)
            grad.setColorAt(1.0, bot_col)
            p.fillPath(path, grad)
        else:
            p.fillPath(path, self.theme.bg)

        # 3. Inner top highlight — a thin lighter line at the top edge gives
        #    a glassy "lit from above" look. Stronger than before — 30 alpha.
        if self.theme.bg.lightness() < 200:
            hl = QColor(255, 255, 255, 25)
            p.setPen(QPen(hl, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(
                rect.x() + 1, rect.y() + 1,
                rect.width() - 2, rect.height() - 2,
                radius - 1, radius - 1,
            )

        # 4. Drop-glow ring when an external drag is hovering us.
        if self._drag_hover:
            pen = QPen(self.theme.drop_glow, 2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)

        self._buttons.clear()
        self._drag_zones.clear()
        self._volume_track_rect = None

        if self.state == State.PILL:
            self._paint_pill(p, rect)
        else:
            self._paint_expanded(p, rect)
            # Status indicators replace the previous separate badges.
            self._draw_status_strip(p, rect)

        # 5. After the paint is complete, recompute which button is under
        #    the cursor. This indexes into the freshly-populated _buttons
        #    list, which is why it has to happen *after* the painters run.
        if self._hover_pos is not None:
            new_idx = -1
            for i, (r, _cb) in enumerate(self._buttons):
                if r.contains(self._hover_pos):
                    new_idx = i
                    break
            if new_idx != self._hovered_idx:
                # Don't trigger another paint here (would loop). Just record;
                # the next mouseMoveEvent will repaint with the corrected idx.
                self._hovered_idx = new_idx

    # ── Pill ──────────────────────────────────────────────────
    def _paint_pill(self, p, rect):
        if self._notif_alive():
            self._paint_notif_pill(p, rect)
        elif self._timer_active():
            self._paint_countdown_pill(
                p, rect,
                self._fmt_secs(int(self.timer_end - time.time())),
                self.theme.amber,
            )
        elif self.media.is_active():
            self._paint_media_pill(p, rect)
        elif self.files:
            self._paint_files_pill(p, rect)

    def _paint_notif_pill(self, p, rect):
        n = self.notif
        # Special-case: volume / mute pills get a richer animation.
        if n.kind == "vol":
            self._paint_volume_pill(p, rect, n)
            return

        h, pad = rect.height(), 6
        dot = QRect(pad + 2, h // 2 - 4, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(n.accent)
        p.drawEllipse(dot)
        text_rect = QRect(dot.right() + 8, 0,
                          rect.width() - dot.right() - 16, h)
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        fm = QFontMetrics(p.font())
        p.drawText(text_rect,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(n.text, Qt.TextElideMode.ElideRight, text_rect.width()))

    def _paint_volume_pill(self, p, rect, n):
        """Animated speaker glyph + level bar. Used for both volume change &
        mute change events."""
        h, pad = rect.height(), 6
        # progress through the pill's own life [0..1] → controls the bar fill anim.
        life = max(0.0, min(1.0, 1.0 - (n.expires_at - time.time()) / VOL_PILL_DURATION))
        # ease-out-cubic
        anim = 1.0 - (1.0 - life) ** 3

        muted = bool(n.data.get("muted"))
        vol = n.data.get("volume")
        is_mute_event = bool(n.data.get("is_mute_event"))

        # ── Speaker icon on the left
        icon = QRect(pad + 2, pad, h - 2 * pad, h - 2 * pad)
        self._draw_speaker_icon(p, icon, n.accent, muted=muted,
                                level=vol if vol is not None else 0.0,
                                anim=anim, animate_strike=is_mute_event)

        # ── Volume level bar on the right (or label for mute)
        bar_x = icon.right() + 12
        bar_w = rect.width() - bar_x - 16
        bar_y = h // 2 - 3
        if vol is None:
            # No level info — just show text
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            p.drawText(QRect(bar_x, 0, bar_w, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       n.text)
            return

        # Level bar
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.bg_track)
        p.drawRoundedRect(bar_x, bar_y, bar_w, 6, 3, 3)
        target_w = int(bar_w * (0.0 if muted else max(0.0, min(1.0, vol))))
        # animate the bar growing in
        cur_w = int(target_w * anim)
        p.setBrush(n.accent)
        if cur_w > 0:
            p.drawRoundedRect(bar_x, bar_y, cur_w, 6, 3, 3)

        # Tiny percentage text right-aligned above
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
        label = "Muted" if muted else f"{int(round(vol * 100))}%"
        p.drawText(QRect(bar_x, 0, bar_w, h - 16),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   label)

    def _paint_countdown_pill(self, p, rect, text, color):
        h, pad = rect.height(), 6
        icon_rect = QRect(pad, pad, h - 2 * pad, h - 2 * pad)
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(icon_rect.adjusted(2, 2, -2, -2))
        c = icon_rect.center()
        p.drawLine(c, QPoint(c.x(), c.y() - icon_rect.width() // 3))

        text_rect = QRect(icon_rect.right() + 8, 0,
                          rect.width() - icon_rect.right() - 16, h)
        p.setPen(color)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        p.drawText(text_rect,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   text)

    def _paint_media_pill(self, p, rect):
        h, pad = rect.height(), 4
        art_rect = QRect(pad, pad, h - 2 * pad, h - 2 * pad)
        self._draw_art(p, art_rect, radius=6, glyph=11)

        eq_rect = QRect(rect.width() - pad - (h - 2 * pad), pad,
                        h - 2 * pad, h - 2 * pad)
        self._draw_eq(p, eq_rect)

        text_rect = QRect(art_rect.right() + 8, 2,
                          eq_rect.left() - art_rect.right() - 16,
                          rect.height() - 6)
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        fm = QFontMetrics(p.font())
        p.drawText(text_rect,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(self.media.title,
                                 Qt.TextElideMode.ElideRight,
                                 text_rect.width()))

        if self.media.duration_s > 0:
            frac = self.media.current_position() / self.media.duration_s
            frac = max(0.0, min(1.0, frac))
            bar_y = rect.bottom() - 3
            bar_x = art_rect.right() + 8
            bar_w = eq_rect.left() - bar_x - 8
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.theme.bg_track)
            p.drawRoundedRect(bar_x, bar_y, bar_w, 2, 1, 1)
            p.setBrush(self.progress_accent() if self.media.is_playing else self.theme.text_dim)
            p.drawRoundedRect(bar_x, bar_y, int(bar_w * frac), 2, 1, 1)

    def _paint_files_pill(self, p, rect):
        """Pill that says 📎 N files held. Click pill to expand to the Files
        card; from the expanded card you can drag rows out."""
        h, pad = rect.height(), 6
        icon = QRect(pad + 2, pad, h - 2 * pad, h - 2 * pad)
        self._draw_paperclip(p, icon, self.theme.green)

        text_rect = QRect(icon.right() + 8, 0,
                          rect.width() - icon.right() - 16, h)
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        n = len(self.files)
        msg = f"{n} file held" if n == 1 else f"{n} files held"
        # show first filename if just one
        if n == 1:
            fm = QFontMetrics(p.font())
            msg = fm.elidedText(self.files[0].name,
                                Qt.TextElideMode.ElideMiddle,
                                text_rect.width())
        p.drawText(text_rect,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   msg)

    # ── Expanded ───────────────────────────────────────────────
    def _paint_expanded(self, p, rect):
        card = self._effective_card()
        if card == Card.MEDIA:
            self._paint_media_card(p, rect)
        elif card == Card.SYSTEM:
            self._paint_system_card(p, rect)
        elif card == Card.AUDIO:
            self._paint_audio_card(p, rect)
        elif card == Card.TIMERS:
            self._paint_timers_card(p, rect)
        elif card == Card.NOTES:
            self._paint_notes_card(p, rect)
        elif card == Card.FILES:
            self._paint_files_card(p, rect)
        elif card == Card.SETTINGS:
            self._paint_settings_card(p, rect)
        elif card == Card.CALENDAR:
            self._paint_calendar_card(p, rect)
        else:
            self._paint_clock_card(p, rect)
        self._paint_card_dots(p, rect)

    def _effective_card(self):
        if self.card != Card.AUTO:
            return self.card
        if self._drag_hover or self.files:
            return Card.FILES
        if self.media.is_active():
            return Card.MEDIA
        return Card.CLOCK

    def _paint_card_dots(self, p, rect):
        active = self._effective_card()
        n = len(CARDS_ORDER)
        gap = 6
        d = 4
        # Each dot gets a larger 20×20 hit-zone for easier clicking.
        hit_w = 20
        total = n * hit_w + (n - 1) * 0
        x = rect.center().x() - total // 2
        y = rect.bottom() - 14
        hovered_card = None
        for c in CARDS_ORDER:
            hit_rect = QRect(x, y - 4, hit_w, 14)
            hovered = self._is_hovered_next()
            if hovered:
                hovered_card = c
            # The visible dot itself — bigger when hovered, brightest when active.
            dot_d = d + 2 if hovered else d
            dot_x = x + (hit_w - dot_d) // 2
            dot_y = y + (4 - (dot_d - d) // 2)
            color = self.theme.text if c == active else self.theme.border
            if hovered and c != active:
                color = self._tint(self.theme.border, 0.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(dot_x, dot_y, dot_d, dot_d)
            self._buttons.append((hit_rect, lambda card=c: self._jump_card(card)))
            x += hit_w

        # Hovered-card label, drawn just above the dots like a tooltip.
        if hovered_card is not None:
            label = self._card_name(hovered_card)
            p.setPen(self.theme.text_dim)
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Medium))
            fm = QFontMetrics(p.font())
            tw = fm.horizontalAdvance(label)
            tx = rect.center().x() - tw // 2
            ty = y - 14
            # Subtle background pill so the label is readable on busy cards
            pad_x = 6
            bg_rect = QRect(tx - pad_x, ty - 1, tw + 2 * pad_x, 14)
            p.setBrush(self.theme.bg_inner)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(bg_rect, 7, 7)
            p.setPen(self.theme.text_dim)
            p.drawText(QRect(tx, ty, tw, 12),
                       Qt.AlignmentFlag.AlignLeft, label)

    @staticmethod
    def _card_name(c):
        return {
            Card.MEDIA: "Music",
            Card.CLOCK: "Clock",
            Card.CALENDAR: "Calendar",
            Card.SYSTEM: "System",
            Card.AUDIO: "Audio",
            Card.TIMERS: "Timers",
            Card.NOTES: "Notes",
            Card.FILES: "Files",
            Card.SETTINGS: "Settings",
        }.get(c, "")

    def _jump_card(self, card):
        self.card = card
        self.update()

    def _draw_status_strip(self, p, rect):
        """Compact strip of state-indicator pills along the very top edge.
        Each pill is just an icon + tiny optional number, painted only when
        the relevant condition is true so the strip stays empty if nothing's
        going on.

        Order (right → left):  pinned  ·  files held  ·  alerts muted  ·
                               timer running  ·  events today  ·  low battery
        """
        chips = []
        if self.pinned:
            chips.append(("📌", "", self.theme.purple))
        if self.files:
            chips.append(("📎", str(len(self.files)), self.theme.green))
        if self.alerts_muted:
            chips.append(("🔕", "", self.theme.red))
        if self._timer_active():
            chips.append(("⏱", "", self.theme.amber))
        # Count today's events
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_n = sum(1 for e in self.events if e.get("date") == today_str)
        if today_n:
            chips.append(("📅", str(today_n), self.theme.cyan))
        # Low battery
        s = self.sysinfo
        if (s.battery_pct is not None
                and s.battery_pct <= self.low_battery_pct
                and not s.battery_plugged):
            chips.append(("🪫", f"{s.battery_pct}%", self.theme.red))

        if not chips:
            return

        # Render right-to-left so the rightmost chip always sticks to the
        # right edge regardless of how many chips there are.
        chip_h = 16
        chip_gap = 4
        x = rect.right() - 8
        y = 6
        # Layout: walk through chips left-to-right BUT compute total width
        # first so we can right-align.
        p.setFont(QFont("Segoe UI Emoji", 8))
        fm_icon = QFontMetrics(p.font())
        widths = []
        for icon, num, _col in chips:
            icon_w = fm_icon.horizontalAdvance(icon)
            num_w = (fm_icon.horizontalAdvance(num) + 2) if num else 0
            widths.append(8 + icon_w + num_w + 8)
        total = sum(widths) + (len(widths) - 1) * chip_gap
        cur_x = rect.right() - 8 - total
        for (icon, num, col), w in zip(chips, widths):
            chip_rect = QRect(cur_x, y, w, chip_h)
            # subtle pill bg in chip color, low alpha
            bg = QColor(col)
            bg.setAlpha(50)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(chip_rect, chip_h / 2, chip_h / 2)
            # icon
            p.setPen(col)
            p.setFont(QFont("Segoe UI Emoji", 8))
            p.drawText(QRect(chip_rect.x() + 5, chip_rect.y(),
                             chip_rect.width() - 10, chip_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       icon)
            if num:
                p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                p.drawText(QRect(chip_rect.x() + 5, chip_rect.y(),
                                 chip_rect.width() - 10, chip_h),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           num)
            cur_x += w + chip_gap

    # ── Clock + Weather card ───────────────────────────────────
    def _paint_clock_card(self, p, rect):
        now = datetime.now()

        # Big time on the left. Hour-minute large, seconds small after it.
        # Seconds tick gives a subtle sign of life — proves the app is alive.
        time_x = 22
        time_y = 14
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 38, QFont.Weight.Light))
        time_str = now.strftime("%H:%M")
        p.drawText(QRect(time_x, time_y, 180, 52),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   time_str)
        # Seconds, dim, smaller, baseline-aligned with the minutes
        fm = QFontMetrics(p.font())
        time_w = fm.horizontalAdvance(time_str)
        p.setPen(self.theme.text_faint)
        p.setFont(QFont("Segoe UI", 14, QFont.Weight.Light))
        p.drawText(QRect(time_x + time_w + 6, time_y + 6, 50, 50),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                   now.strftime(":%S"))

        # Date below time
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(QRect(time_x, 68, 220, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%A · %B %d"))

        # Vertical divider between time and weather
        div_x = rect.width() // 2 + 8
        p.setPen(QPen(self.theme.border, 1))
        p.drawLine(div_x, 22, div_x, 84)

        wx_x = div_x + 16
        if self.weather.is_fresh():
            # Weather glyph (large emoji-style char) on the left of the temp
            glyph = self._weather_glyph(self.weather.desc)
            p.setPen(self.theme.amber)
            p.setFont(QFont("Segoe UI Emoji", 26))
            p.drawText(QRect(wx_x, 14, 44, 50),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       glyph)
            # Temperature
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 28, QFont.Weight.Light))
            p.drawText(QRect(wx_x + 48, 14, rect.width() - wx_x - 68, 50),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{self.weather.temp_c}°C")
            # Description
            p.setPen(self.theme.text_dim)
            p.setFont(QFont("Segoe UI", 10))
            fm2 = QFontMetrics(p.font())
            avail = rect.width() - wx_x - 20
            p.drawText(QRect(wx_x, 68, avail, 22),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm2.elidedText(self.weather.desc.title(),
                                      Qt.TextElideMode.ElideRight, avail))
        else:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRect(wx_x, 30, rect.width() - wx_x - 20, 22),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "Weather offline")

        # Hint line at bottom — only show if user hasn't seen the island
        # do its thing yet. Otherwise replace with battery + net at-a-glance.
        self._paint_clock_footer(p, rect)

    def _paint_clock_footer(self, p, rect):
        """Compact bottom-strip of useful info: battery, CPU, network."""
        s = self.sysinfo
        chunks = []
        if s.battery_pct is not None:
            icon = "⚡" if s.battery_plugged else "🔋"
            chunks.append(f"{icon} {s.battery_pct}%")
        if s.cpu:
            chunks.append(f"CPU {s.cpu}%")
        if s.net_down_bps + s.net_up_bps > 1000:
            chunks.append(f"↓ {_fmt_bps(s.net_down_bps)}")
        if HAS_HOTKEY:
            chunks.append("Ctrl+Shift+Space")
        if not chunks:
            chunks = ["scroll to switch cards"]
        p.setPen(self.theme.text_faint)
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRect(22, rect.bottom() - 32, rect.width() - 44, 14),
                   Qt.AlignmentFlag.AlignLeft,
                   "   ·   ".join(chunks))

    @staticmethod
    def _weather_glyph(desc):
        """Pick a sensible emoji-style glyph for a weather description."""
        d = (desc or "").lower()
        if "thunder" in d or "storm" in d:    return "⛈"
        if "snow" in d or "blizzard" in d:    return "❄"
        if "rain" in d or "drizzle" in d:     return "🌧"
        if "sleet" in d or "hail" in d:       return "🌨"
        if "fog" in d or "mist" in d or "haz" in d: return "🌫"
        if "wind" in d:                       return "💨"
        if "cloud" in d or "overcast" in d:   return "☁"
        if "partly" in d:                     return "⛅"
        if "sun" in d or "clear" in d or "fair" in d: return "☀"
        return "◯"

    # ── Media card ─────────────────────────────────────────────
    def _paint_media_card(self, p, rect):
        if not self.media.title:
            self._paint_clock_card(p, rect)
            return
        pad = 14
        # Slightly smaller art so we have room for a visualizer beneath it
        art_size = EXPAND_H - 2 * pad - 36
        art = QRect(pad, pad, art_size, art_size)
        self._draw_art(p, art, radius=10, glyph=24)

        # Real audio visualizer below the art, same width as the art.
        viz = QRect(art.x(), art.bottom() + 4, art.width(), 18)
        self._draw_visualizer(p, viz)

        info_x = art.right() + 14
        info_w = rect.width() - info_x - pad
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        fm = QFontMetrics(p.font())
        p.drawText(QRect(info_x, pad + 2, info_w, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.media.title, Qt.TextElideMode.ElideRight, info_w))
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 10))
        fm2 = QFontMetrics(p.font())
        p.drawText(QRect(info_x, pad + 24, info_w, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm2.elidedText(self.media.artist or "",
                                  Qt.TextElideMode.ElideRight, info_w))

        bar_x = info_x
        bar_y = pad + 50
        bar_w = info_w
        if self.media.duration_s > 0:
            frac = self.media.current_position() / self.media.duration_s
            frac = max(0.0, min(1.0, frac))
        else:
            frac = 0.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.bg_track)
        p.drawRoundedRect(bar_x, bar_y, bar_w, 3, 1.5, 1.5)
        p.setBrush(self.progress_accent() if self.media.is_playing else self.theme.text_dim)
        p.drawRoundedRect(bar_x, bar_y, int(bar_w * frac), 3, 1.5, 1.5)

        p.setPen(self.theme.text_faint)
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRect(bar_x, bar_y + 6, 60, 14),
                   Qt.AlignmentFlag.AlignLeft,
                   self._fmt_secs(int(self.media.current_position())))
        p.drawText(QRect(bar_x + bar_w - 60, bar_y + 6, 60, 14),
                   Qt.AlignmentFlag.AlignRight,
                   self._fmt_secs(int(self.media.duration_s)))

        ctrl_y = bar_y + 28
        ctrl_size = 30
        gap = 14
        total = 3 * ctrl_size + 2 * gap
        cx = info_x + (bar_w - total) // 2
        prev_r = QRect(cx, ctrl_y, ctrl_size, ctrl_size)
        play_r = QRect(cx + ctrl_size + gap, ctrl_y, ctrl_size, ctrl_size)
        next_r = QRect(cx + 2 * (ctrl_size + gap), ctrl_y, ctrl_size, ctrl_size)
        self._draw_button(p, prev_r, _draw_prev, callback=lambda: media_send("prev"))
        self._draw_button(
            p, play_r,
            _draw_pause if self.media.is_playing else _draw_play,
            filled=True,
            callback=lambda: media_send("play_pause"),
        )
        self._draw_button(p, next_r, _draw_next, callback=lambda: media_send("next"))

    # ── System card (CPU, RAM, Battery, Network) ───────────────
    def _paint_system_card(self, p, rect):
        s = self.sysinfo
        title_pad = 18
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, 10, rect.width() - 2 * title_pad, 20),
                   Qt.AlignmentFlag.AlignLeft, "System")

        rows = [
            ("CPU", s.cpu, self.theme.blue, f"{s.cpu}%"),
            ("RAM", s.ram, self.theme.amber, f"{s.ram}%"),
        ]
        if s.battery_pct is not None:
            color = self.theme.green if s.battery_pct > self.low_battery_pct else self.theme.red
            tag = f"{s.battery_pct}%"
            if s.battery_plugged:
                tag += "  ⚡"
            elif s.battery_secs:
                tag += f"  · {self._fmt_hm(s.battery_secs)}"
            rows.append(("Battery", s.battery_pct, color, tag))

        list_y = 32
        net_strip_h = 40   # bottom strip for network sparkline
        rows_area_h = rect.height() - list_y - net_strip_h - 16
        row_h = rows_area_h // max(1, len(rows))
        for i, (label, pct, col, tag) in enumerate(rows):
            y = list_y + i * row_h
            p.setPen(self.theme.text_dim)
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            p.drawText(QRect(title_pad, y, 70, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       label)
            bar_x = title_pad + 60
            bar_w = rect.width() - bar_x - 130
            bar_h = 8
            bar_y = y + row_h // 2 - bar_h // 2
            # Track + fill, rounded
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.theme.bg_track)
            p.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, bar_h / 2, bar_h / 2)
            fill_w = int(bar_w * pct / 100)
            if fill_w > 0:
                p.setBrush(col)
                p.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, bar_h / 2, bar_h / 2)
            # Value, right-aligned
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            p.drawText(QRect(bar_x + bar_w + 8, y, 120, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag)

        # Network strip at the bottom with a tiny sparkline.
        net_y = rect.height() - net_strip_h
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, net_y, 50, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Net")
        # Sparkline area
        spark_x = title_pad + 52
        spark_w = rect.width() - spark_x - 168
        spark_y = net_y + 4
        spark_h = 18
        self._draw_sparkline(p, QRect(spark_x, spark_y, spark_w, spark_h),
                             self._net_down_history, self._net_hist_idx,
                             self.theme.cyan)
        # Up/down labels right-aligned
        net_text_x = spark_x + spark_w + 8
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRect(net_text_x, net_y, 160, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"↓ {_fmt_bps(s.net_down_bps)}")
        p.drawText(QRect(net_text_x, net_y + 14, 160, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"↑ {_fmt_bps(s.net_up_bps)}")

    def _draw_sparkline(self, p, rect, history, idx, color):
        """Tiny line graph from a circular history buffer. Auto-scales to
        the max value in the window so it always uses the full height."""
        n = len(history)
        if n < 2:
            return
        # Ordered samples, oldest → newest
        samples = [history[(idx + i) % n] for i in range(n)]
        peak = max(samples) or 1.0
        # Draw track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.bg_track)
        p.drawRoundedRect(rect, 3, 3)
        # Filled area under the line
        from PyQt6.QtGui import QPolygonF
        from PyQt6.QtCore import QPointF
        pts = []
        for i, v in enumerate(samples):
            x = rect.x() + i * (rect.width() / (n - 1))
            y = rect.bottom() - (v / peak) * (rect.height() - 2)
            pts.append(QPointF(x, y))
        # Close the polygon at the bottom for fill
        fill_pts = list(pts) + [
            QPointF(rect.right(), rect.bottom()),
            QPointF(rect.x(), rect.bottom()),
        ]
        fill_color = QColor(color)
        fill_color.setAlpha(60)
        p.setBrush(fill_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF(fill_pts))
        # The actual line
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])

    # ── Audio card (volume slider + mute toggle) ───────────────
    def _paint_audio_card(self, p, rect):
        title_pad = 14
        info = self.audio
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title = "Audio"
        if info.muted:
            title += "  ·  Muted"
        elif info.volume is not None:
            title += f"  ·  {int(round(info.volume * 100))}%"
        p.drawText(QRect(title_pad, 10, rect.width() - 2 * title_pad, 20),
                   Qt.AlignmentFlag.AlignLeft, title)

        # Speaker icon on the left
        icon_size = 56
        icon = QRect(title_pad + 4, 38, icon_size, icon_size)
        accent = (self.theme.red if info.muted
                  else self.theme.blue if info.volume is not None
                  else self.theme.text_faint)
        vol_for_icon = info.volume if info.volume is not None else 0.0
        self._draw_speaker_icon(p, icon, accent,
                                muted=bool(info.muted),
                                level=vol_for_icon, anim=1.0,
                                animate_strike=False)

        # Slider area
        slider_x = icon.right() + 18
        slider_w = rect.width() - slider_x - title_pad - 80
        slider_y = icon.center().y() - 4

        if info.volume is None:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRect(slider_x, icon.y(), slider_w, icon.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "Volume read unavailable\nUse  ⇧  /  ⇩  buttons or system keys")
        else:
            track = QRect(slider_x, slider_y, slider_w, 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.theme.bg_track)
            p.drawRoundedRect(track, 4, 4)
            fill_w = int(slider_w * (0.0 if info.muted else info.volume))
            p.setBrush(accent)
            if fill_w > 0:
                p.drawRoundedRect(slider_x, slider_y, fill_w, 8, 4, 4)
            # Knob
            kx = slider_x + fill_w
            knob = QRect(kx - 7, slider_y - 4, 14, 16)
            p.setBrush(self.theme.text)
            p.drawRoundedRect(knob, 4, 4)
            # Make whole track clickable for setting volume
            click_rect = QRect(slider_x, slider_y - 8, slider_w, 24)
            self._buttons.append((click_rect,
                                  lambda r=click_rect:
                                  self._volume_track_click(r)))
            # Same rect doubles as the "scroll-here-for-volume" zone.
            self._volume_track_rect = click_rect
            # Percent label
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Medium))
            label = "Muted" if info.muted else f"{int(round(info.volume * 100))}%"
            p.drawText(QRect(slider_x + slider_w + 8, slider_y - 6, 70, 20),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       label)

        # Buttons row at the bottom
        btn_h = 24
        btn_y = rect.height() - btn_h - 22
        bw_small, bw_wide = 56, 86
        gap = 8
        total = bw_small * 2 + bw_wide + gap * 2
        x = (rect.width() - total) // 2

        bdown = QRect(x, btn_y, bw_small, btn_h)
        x += bw_small + gap
        bmute = QRect(x, btn_y, bw_wide, btn_h)
        x += bw_wide + gap
        bup = QRect(x, btn_y, bw_small, btn_h)

        self._draw_label_button(p, bdown, "−",
                                self.theme.text,
                                lambda: self.step_volume(-VOLUME_STEP))
        mute_label = "Unmute" if info.muted else "Mute"
        mute_color = self.theme.red if info.muted else self.theme.text
        self._draw_label_button(p, bmute, mute_label,
                                mute_color, self.toggle_system_mute)
        self._draw_label_button(p, bup, "+",
                                self.theme.text,
                                lambda: self.step_volume(VOLUME_STEP))

    def _volume_track_click(self, click_rect):
        """Translate the most-recent click position within the track to a
        volume level."""
        pos = self.mapFromGlobal(QCursor.pos())
        x = pos.x() - click_rect.x()
        frac = max(0.0, min(1.0, x / max(1, click_rect.width())))
        self.set_volume_frac(frac)

    # ── Timers card (timer / stopwatch) ────────────────────────
    def _paint_timers_card(self, p, rect):
        cols = 2
        col_w = rect.width() // cols
        for i, drawer in enumerate((self._draw_timer_col,
                                    self._draw_stopwatch_col)):
            col_rect = QRect(i * col_w, 0, col_w, rect.height() - 16)
            drawer(p, col_rect)
            if i > 0:
                p.setPen(self.theme.border)
                p.drawLine(col_rect.x(), 16,
                           col_rect.x(), rect.height() - 28)

    def _draw_timer_col(self, p, r):
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(r.x(), r.y() + 6, r.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "TIMER")
        active = self._timer_active()
        if active:
            # Draw a soft progress ring around the time. Need to know the
            # original duration of the timer to know how full to draw — we
            # don't store that, so use a simple "minutes-remaining" angle
            # mapped against 60 minutes as a visual estimate.
            remaining = int(self.timer_end - time.time())
            ring_size = 80
            ring_x = r.x() + (r.width() - ring_size) // 2
            ring_y = r.y() + 22
            # Background ring
            p.setPen(QPen(self.theme.bg_track, 4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(ring_x, ring_y, ring_size, ring_size)
            # Progress arc — assumes ≤ 1hr; longer timers just stay nearly full.
            full_s = min(3600, max(60, remaining * 2))
            frac = max(0.0, min(1.0, remaining / max(1, full_s)))
            pen = QPen(self.theme.amber, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            # Qt arc: angles are 16ths of degree; start at 90 (top), go negative for CW.
            p.drawArc(ring_x, ring_y, ring_size, ring_size,
                      90 * 16, -int(360 * 16 * frac))
            # Time text inside the ring
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 16, QFont.Weight.Light))
            p.drawText(QRect(ring_x, ring_y, ring_size, ring_size),
                       Qt.AlignmentFlag.AlignCenter,
                       self._fmt_secs(remaining))

            btn_w, btn_h = 88, 22
            btn = QRect(r.x() + (r.width() - btn_w) // 2,
                        r.y() + r.height() - btn_h - 2, btn_w, btn_h)
            self._draw_label_button(p, btn, "Cancel", self.theme.amber,
                                    self.cancel_timer)
        else:
            # Big idle "00:00" — same line-weight as active timer for consistency
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 28, QFont.Weight.Light))
            p.drawText(QRect(r.x(), r.y() + 22, r.width(), 36),
                       Qt.AlignmentFlag.AlignCenter, "00:00")

            # Preset chips
            chip_y = r.y() + 64
            chip_h = 22
            chips = [("5m", 5 * 60), ("15m", 15 * 60), ("30m", 30 * 60)]
            chip_w = 38
            gap = 8
            total_w = 3 * chip_w + 2 * gap
            x0 = r.x() + (r.width() - total_w) // 2
            for i, (label, secs) in enumerate(chips):
                cr = QRect(x0 + i * (chip_w + gap), chip_y, chip_w, chip_h)
                self._draw_chip(p, cr, label, self.theme.amber,
                                lambda s=secs: self.start_timer(s))

            btn_w, btn_h = 88, 22
            btn = QRect(r.x() + (r.width() - btn_w) // 2,
                        r.y() + r.height() - btn_h - 2, btn_w, btn_h)
            self._draw_label_button(p, btn, "Custom…", self.theme.text,
                                    self.prompt_timer)

    def _draw_stopwatch_col(self, p, r):
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(r.x(), r.y() + 6, r.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "STOPWATCH")
        sw = self.stopwatch
        running = sw.running

        # Big time
        color = (self.theme.blue if running
                 else (self.theme.text if sw.is_active() else self.theme.text_faint))
        p.setPen(color)
        p.setFont(QFont("Segoe UI", 28, QFont.Weight.Light))
        total = sw.total()
        p.drawText(QRect(r.x(), r.y() + 26, r.width(), 36),
                   Qt.AlignmentFlag.AlignCenter,
                   self._fmt_secs(int(total)))
        # Tiny pulsing dot when running, indicates "live"
        if running:
            pulse = (math.sin(time.time() * 4) + 1) * 0.5
            alpha = int(120 + pulse * 100)
            dot_color = QColor(color)
            dot_color.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot_color)
            p.drawEllipse(r.x() + r.width() // 2 + 50, r.y() + 38, 6, 6)

        # Subtitle: milliseconds when running, hint otherwise
        if running or sw.is_active():
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 9))
            ms = int((total - int(total)) * 100)
            p.drawText(QRect(r.x(), r.y() + 64, r.width(), 18),
                       Qt.AlignmentFlag.AlignCenter,
                       f".{ms:02d}")

        # Buttons
        btn_w, btn_h = 70, 22
        gap = 8
        total_w = 2 * btn_w + gap
        x = r.x() + (r.width() - total_w) // 2
        b1 = QRect(x, r.y() + r.height() - btn_h - 2, btn_w, btn_h)
        b2 = QRect(x + btn_w + gap, r.y() + r.height() - btn_h - 2, btn_w, btn_h)
        self._draw_label_button(
            p, b1, "Pause" if running else "Start",
            self.theme.blue if running else self.theme.green,
            sw.toggle,
        )
        self._draw_label_button(p, b2, "Reset", self.theme.text_dim, sw.reset)

    # ── Calendar card  (NEW) ──────────────────────────────────
    def _paint_calendar_card(self, p, rect):
        """Mini month grid on the left + upcoming events list on the right."""
        title_pad = 12
        today = datetime.now().date()
        viewing = date(self._cal_year, self._cal_month, 1)

        # ── Header: month name + nav arrows ─────────────────
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        month_label = viewing.strftime("%B %Y")
        p.drawText(QRect(title_pad, 6, 200, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   month_label)

        # Prev / next / today buttons
        nav_y = 8
        nav_w = 22
        nav_left = QRect(title_pad + 138, nav_y, nav_w, 18)
        nav_right = QRect(title_pad + 162, nav_y, nav_w, 18)
        self._draw_label_button(p, nav_left, "‹", self.theme.text,
                                self._cal_prev_month)
        self._draw_label_button(p, nav_right, "›", self.theme.text,
                                self._cal_next_month)

        # "+ Event" on the right
        add_w = 78
        add_r = QRect(rect.right() - add_w - title_pad, nav_y, add_w, 18)
        self._draw_label_button(p, add_r, "+ Event", self.theme.green,
                                self.add_event_dialog)

        # ── Month grid on the left ──────────────────────────
        grid_x = title_pad
        grid_y = 32
        grid_w = rect.width() // 2 - title_pad
        grid_h = rect.height() - grid_y - 26

        # Weekday header (Mon..Sun), then 6 rows of 7 cells.
        cell_w = grid_w / 7
        cell_h = (grid_h - 14) / 6.4   # leave space for header
        p.setPen(self.theme.text_faint)
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
        for i, name in enumerate(["M", "T", "W", "T", "F", "S", "S"]):
            cx = grid_x + int(i * cell_w)
            p.drawText(QRect(cx, grid_y, int(cell_w), 12),
                       Qt.AlignmentFlag.AlignCenter, name)

        # Build the month — calendar.Calendar with Monday as first weekday.
        cal = _cal.Calendar(firstweekday=0)
        weeks = cal.monthdatescalendar(self._cal_year, self._cal_month)

        # Set of dates that have events, for the dots
        event_dates = {e["date"] for e in self.events}

        for row, week in enumerate(weeks[:6]):
            for col, d in enumerate(week):
                cx = grid_x + col * cell_w
                cy = grid_y + 14 + row * cell_h
                cell = QRect(int(cx), int(cy),
                             int(cell_w), int(cell_h))
                in_month = d.month == self._cal_month
                is_today = (d == today)
                iso = d.isoformat()
                has_events = iso in event_dates
                is_selected = (self._cal_selected == iso)

                # Background — today gets accent fill; selected gets bg_inner
                p.setPen(Qt.PenStyle.NoPen)
                if is_today:
                    bg = self.theme.amber
                    p.setBrush(bg)
                    pad = 3
                    bg_rect = cell.adjusted(pad, pad, -pad, -pad)
                    p.drawRoundedRect(bg_rect, 5, 5)
                elif is_selected:
                    p.setBrush(self.theme.bg_inner)
                    pad = 3
                    bg_rect = cell.adjusted(pad, pad, -pad, -pad)
                    p.drawRoundedRect(bg_rect, 5, 5)

                # Day number
                if is_today:
                    p.setPen(self.theme.bg)
                    p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                elif in_month:
                    p.setPen(self.theme.text)
                    p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
                else:
                    p.setPen(self.theme.text_faint)
                    p.setFont(QFont("Segoe UI", 9))
                p.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(d.day))

                # Event dot (small, bottom of cell)
                if has_events and in_month:
                    dot_color = (self.theme.bg if is_today
                                 else self.theme.cyan)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(dot_color)
                    dot_d = 3
                    p.drawEllipse(int(cx + cell_w / 2 - dot_d / 2),
                                  int(cy + cell_h - 6),
                                  dot_d, dot_d)

                # Whole cell is clickable to select that day
                self._buttons.append(
                    (cell, lambda iso=iso: self._select_cal_date(iso))
                )

        # ── Right column: upcoming events ───────────────────
        right_x = rect.width() // 2 + 8
        right_w = rect.width() - right_x - title_pad
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        # If a date is selected on the grid, show that date's events; otherwise
        # show next 3 upcoming events from today.
        if self._cal_selected:
            try:
                sel_d = date.fromisoformat(self._cal_selected)
                label_text = sel_d.strftime("%a %b %d").upper()
            except Exception:
                label_text = "UPCOMING"
            shown_events = events_on_date(self.events, self._cal_selected)
        else:
            label_text = "UPCOMING"
            shown_events = upcoming_events(self.events, EVENTS_UPCOMING)

        p.drawText(QRect(right_x, 32, right_w, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   label_text)

        if not shown_events:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 9))
            empty_msg = ("No events on this day"
                         if self._cal_selected else "No upcoming events")
            p.drawText(QRect(right_x, 56, right_w, 30),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       empty_msg)
        else:
            row_y = 48
            row_h = 32
            for i, e in enumerate(shown_events):
                ry = row_y + i * row_h
                row = QRect(right_x, ry, right_w, row_h - 4)
                # Row card
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self.theme.bg_inner)
                p.drawRoundedRect(row, 6, 6)
                # Accent stripe
                stripe_color = self._accent(e.get("color", "cyan"), "cyan")
                tab = QRect(row.x(), row.y() + 4, 3, row.height() - 8)
                p.setBrush(stripe_color)
                p.drawRoundedRect(tab, 1.5, 1.5)

                # Date+time on the left (compact)
                try:
                    ev_d = date.fromisoformat(e["date"])
                    if ev_d == today:
                        date_label = "Today"
                    elif ev_d == today + timedelta(days=1):
                        date_label = "Tomorrow"
                    else:
                        date_label = ev_d.strftime("%a %b %d")
                except Exception:
                    date_label = e["date"]
                time_label = e.get("time") or ""

                txt_x = row.x() + 10
                p.setPen(self.theme.text_dim)
                p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
                line1 = date_label + ("  ·  " + time_label if time_label else "")
                p.drawText(QRect(txt_x, row.y() + 3, row.width() - 30, 12),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           line1)
                # Event text
                p.setPen(self.theme.text)
                p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
                fm = QFontMetrics(p.font())
                avail = row.width() - 14 - 22
                p.drawText(QRect(txt_x, row.y() + 14, avail, 14),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           fm.elidedText(e["text"],
                                         Qt.TextElideMode.ElideRight,
                                         avail))

                # Delete X on the right
                x_btn = QRect(row.right() - 22, row.y() + (row.height() - 16) // 2,
                              16, 16)
                self._draw_x_button(p, x_btn, self.theme.text_faint,
                                    lambda ev=e: self._delete_event(ev))

    def _cal_prev_month(self):
        m = self._cal_month - 1
        y = self._cal_year
        if m == 0:
            m = 12
            y -= 1
        self._cal_month = m
        self._cal_year = y
        self._cal_selected = None
        self.update()

    def _cal_next_month(self):
        m = self._cal_month + 1
        y = self._cal_year
        if m == 13:
            m = 1
            y += 1
        self._cal_month = m
        self._cal_year = y
        self._cal_selected = None
        self.update()

    def _select_cal_date(self, iso):
        # Toggle off when clicking the same date twice
        if self._cal_selected == iso:
            self._cal_selected = None
        else:
            self._cal_selected = iso
        self.update()

    def add_event_dialog(self):
        """Two-step dialog: ask date+time, then event text."""
        from PyQt6.QtWidgets import QInputDialog
        # Pre-fill date — selected date if any, else today
        default = self._cal_selected or datetime.now().strftime("%Y-%m-%d")
        date_str, ok = QInputDialog.getText(
            self, "New Event",
            "Date  (YYYY-MM-DD)  and optional time  (HH:MM)\n"
            "e.g.  2026-05-15        or        2026-05-15 14:30",
            text=default,
        )
        if not ok or not date_str.strip():
            return
        parts = date_str.strip().split()
        ev_date = parts[0]
        ev_time = parts[1] if len(parts) > 1 else ""
        # Validate date
        try:
            date.fromisoformat(ev_date)
        except Exception:
            self._notify("event", f"Bad date: {ev_date}",
                         self.theme.red, duration_s=2)
            return

        text, ok = QInputDialog.getText(self, "New Event", "What's happening?")
        if not ok or not text.strip():
            return
        self.events.insert(0, {
            "date": ev_date,
            "time": ev_time,
            "text": text.strip(),
            "color": "cyan",
            "ts": time.time(),
        })
        save_events(self.events)
        # Jump the grid to the event's month so the user can see it.
        try:
            d = date.fromisoformat(ev_date)
            self._cal_month = d.month
            self._cal_year = d.year
        except Exception:
            pass
        self._notify("event", f"Event saved  ·  {ev_date}",
                     self.theme.green, duration_s=2)
        self.update()

    def _delete_event(self, event):
        if event in self.events:
            self.events.remove(event)
            save_events(self.events)
            self.update()

    def clear_events(self):
        self.events = []
        save_events(self.events)
        self.update()

    # ── Notes card ─────────────────────────────────────────────
    def _paint_notes_card(self, p, rect):
        title_pad = 14
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title = f"Notes  ·  {len(self.notes)}" if self.notes else "Notes"
        p.drawText(QRect(title_pad, 10, rect.width() - 2 * title_pad, 20),
                   Qt.AlignmentFlag.AlignLeft, title)

        add_w, add_h = 80, 22
        add_r = QRect(rect.right() - add_w - title_pad, 10, add_w, add_h)
        self._draw_label_button(p, add_r, "+ New note", self.theme.green,
                                self.add_note_dialog)

        if not self.notes:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRect(title_pad, 50, rect.width() - 2 * title_pad, 40),
                       Qt.AlignmentFlag.AlignCenter,
                       "No notes yet — tap '+ New note' to jot one down")
            return

        list_top = 38
        list_bot = rect.bottom() - 26
        per = max(28, (list_bot - list_top) // NOTES_VISIBLE)
        for i, note in enumerate(self.notes[:NOTES_VISIBLE]):
            y = list_top + i * per
            row = QRect(title_pad, y, rect.width() - 2 * title_pad, per - 4)

            # Row background — gives notes a "card" feel.
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.theme.bg_inner)
            p.drawRoundedRect(row, 6, 6)

            # Coloured accent strip on the left edge — like a sticky note tab.
            tab_w = 3
            tab_r = QRect(row.x(), row.y() + 4, tab_w, row.height() - 8)
            tab_color = [self.theme.amber, self.theme.cyan, self.theme.pink][i % 3]
            p.setBrush(tab_color)
            p.drawRoundedRect(tab_r, tab_w / 2, tab_w / 2)

            # Timestamp top-right (dim, small)
            ts = note.get("ts", 0)
            try:
                stamp = datetime.fromtimestamp(ts).strftime("%b %d · %H:%M")
            except Exception:
                stamp = ""
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRect(row.right() - 110, row.y() + 2, 100, 14),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       stamp)

            # Note text (first line emphasised; rest is preview)
            txt = note.get("text", "")
            first_line = txt.split("\n", 1)[0]
            rest = txt[len(first_line):].lstrip("\n")[:60]
            text_x = row.x() + tab_w + 10
            text_w = row.width() - tab_w - 10 - 32  # right margin for X button
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            fm = QFontMetrics(p.font())
            p.drawText(QRect(text_x, row.y() + 4, text_w, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(first_line, Qt.TextElideMode.ElideRight, text_w))
            if rest:
                p.setPen(self.theme.text_dim)
                p.setFont(QFont("Segoe UI", 8))
                fm2 = QFontMetrics(p.font())
                p.drawText(QRect(text_x, row.y() + 20, text_w, 14),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           fm2.elidedText(rest, Qt.TextElideMode.ElideRight, text_w))

            # Delete button
            x_btn = QRect(row.right() - 26, row.y() + (row.height() - 18) // 2,
                          18, 18)
            self._draw_x_button(p, x_btn, self.theme.text_faint,
                                lambda idx=i: self._delete_note(idx))

        if len(self.notes) > NOTES_VISIBLE:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRect(title_pad, rect.bottom() - 30,
                             rect.width() - 2 * title_pad, 14),
                       Qt.AlignmentFlag.AlignCenter,
                       f"+ {len(self.notes) - NOTES_VISIBLE} older note{'s' if len(self.notes) - NOTES_VISIBLE != 1 else ''}")

    def _delete_note(self, idx):
        if 0 <= idx < len(self.notes):
            del self.notes[idx]
            save_notes(self.notes)
            self.update()

    # ── Files card (NEW v4) ────────────────────────────────────
    def _paint_files_card(self, p, rect):
        title_pad = 14
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        title_text = ("Drop files here…" if self._drag_hover
                      else f"Files  ·  {len(self.files)}")
        p.drawText(QRect(title_pad, 10, rect.width() - 2 * title_pad, 20),
                   Qt.AlignmentFlag.AlignLeft, title_text)

        # Clear button (top-right) — only when we have files
        if self.files:
            cw, ch = 70, 22
            cr = QRect(rect.right() - cw - title_pad, 10, cw, ch)
            self._draw_label_button(p, cr, "Clear All", self.theme.red,
                                    self.clear_files)

        if not self.files:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 10))
            msg = ("Release to attach the file" if self._drag_hover
                   else "Drag any file from your file manager onto the\n"
                        "island to hold it. Then drag a row out to drop it\n"
                        "into a website, email, or any other app.")
            p.drawText(QRect(title_pad, 36,
                             rect.width() - 2 * title_pad,
                             rect.height() - 60),
                       Qt.AlignmentFlag.AlignCenter, msg)
            return

        list_top = 38
        list_bot = rect.bottom() - 24
        per = max(28, (list_bot - list_top) // FILES_VISIBLE)
        for i, f in enumerate(self.files[:FILES_VISIBLE]):
            y = list_top + i * per
            row = QRect(title_pad, y, rect.width() - 2 * title_pad, per - 4)

            # Row background
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.theme.bg_inner)
            p.drawRoundedRect(row, 6, 6)

            # Icon
            icon_r = QRect(row.x() + 6, row.y() + (row.height() - 18) // 2,
                           18, 18)
            self._draw_file_icon(p, icon_r, self.theme.blue)

            # Filename
            name_x = icon_r.right() + 8
            name_w = row.width() - 6 - icon_r.width() - 8 - 80
            p.setPen(self.theme.text)
            p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
            fm = QFontMetrics(p.font())
            p.drawText(QRect(name_x, row.y(), name_w, row.height() // 2 + 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                       fm.elidedText(f.name, Qt.TextElideMode.ElideMiddle, name_w))

            # Size + drag hint
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 8))
            sub = f.size_str()
            if not f.exists():
                sub = "(missing)"
            p.drawText(QRect(name_x, row.y() + row.height() // 2 - 2,
                             name_w, row.height() // 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       f"{sub}   ·   drag me")

            # Remove button on the right
            x_btn = QRect(row.right() - 28, row.y() + (row.height() - 22) // 2,
                          22, 22)
            self._draw_x_button(p, x_btn, self.theme.text_faint,
                                lambda idx=i: self.remove_file(idx))

            # Mark the file row (excluding the X button) as a drag source
            drag_zone = QRect(row.x(), row.y(),
                              row.width() - 32, row.height())
            self._drag_zones.append(
                (drag_zone, lambda fp=f.path: self._mime_for_files([fp]))
            )

        if len(self.files) > FILES_VISIBLE:
            p.setPen(self.theme.text_faint)
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRect(title_pad, rect.bottom() - 32,
                             rect.width() - 2 * title_pad, 14),
                       Qt.AlignmentFlag.AlignCenter,
                       f"+ {len(self.files) - FILES_VISIBLE} more — remove some to see them")

    # ── Settings card  (NEW) ───────────────────────────────────
    def _paint_settings_card(self, p, rect):
        title_pad = 14
        # Title with current page
        page_label = "General" if self._settings_page == 0 else "Colours"
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, 8, rect.width() - 2 * title_pad, 18),
                   Qt.AlignmentFlag.AlignLeft, f"Settings  ·  {page_label}")

        # Page navigation arrows (top-right area)
        nav_w, nav_h = 22, 20
        nav_y = 8
        nav_left = QRect(rect.right() - 2 * nav_w - 4 - 60 - title_pad - 6,
                         nav_y, nav_w, nav_h)
        nav_right = QRect(nav_left.right() + 4, nav_y, nav_w, nav_h)
        self._draw_label_button(p, nav_left, "‹", self.theme.text,
                                lambda: self._set_settings_page(0))
        self._draw_label_button(p, nav_right, "›", self.theme.text,
                                lambda: self._set_settings_page(1))

        # Reset button (top-right)
        rw, rh = 60, 20
        rr = QRect(rect.right() - rw - title_pad, 8, rw, rh)
        self._draw_label_button(p, rr, "Reset", self.theme.red,
                                self.reset_preferences)

        if self._settings_page == 0:
            self._paint_settings_general(p, rect, title_pad)
        else:
            self._paint_settings_colours(p, rect, title_pad)

        # Footer hint with page dots
        p.setPen(self.theme.text_faint)
        p.setFont(QFont("Segoe UI", 8))
        hint = ("page 1/2  ·  click ‹ › to flip"
                if self._settings_page == 0 else
                "page 2/2  ·  click ‹ › to flip")
        p.drawText(QRect(title_pad, rect.bottom() - 30,
                         rect.width() - 2 * title_pad, 14),
                   Qt.AlignmentFlag.AlignLeft, hint)

    def _set_settings_page(self, n):
        n = max(0, min(1, int(n)))
        if n != self._settings_page:
            self._settings_page = n
            self.update()

    def _paint_settings_general(self, p, rect, title_pad):
        # ── Theme picker row (clickable colour dots) ─────────
        row_y = 32
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, row_y, 50, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "THEME")
        dot_d = 18
        gap = 6
        n = len(THEME_ORDER)
        dots_w = n * dot_d + (n - 1) * gap
        dots_x = rect.right() - dots_w - title_pad
        for i, name in enumerate(THEME_ORDER):
            t = THEMES[name]
            x = dots_x + i * (dot_d + gap)
            y = row_y - 1
            dot_r = QRect(x, y, dot_d, dot_d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.bg)
            p.drawEllipse(dot_r)
            p.setPen(QPen(t.amber, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(dot_r.adjusted(3, 3, -3, -3))
            if name == self.theme.name:
                p.setPen(QPen(self.theme.text, 2))
                p.drawEllipse(dot_r.adjusted(-2, -2, 2, 2))
            self._buttons.append((dot_r, lambda n=name: self.set_theme(n)))

        # ── Toggles row ──────────────────────────────────────
        row2_y = 60
        toggle_lbl_x = title_pad
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRect(toggle_lbl_x, row2_y, 160, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Hide on fullscreen")
        sw1 = QRect(toggle_lbl_x + 130, row2_y + 2, 32, 16)
        self._draw_toggle(p, sw1, self.hide_on_fullscreen,
                          self.toggle_hide_on_fullscreen)

        toggle2_x = rect.width() // 2 + 8
        p.setPen(self.theme.text_dim)
        p.drawText(QRect(toggle2_x, row2_y, 160, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Mute alerts")
        sw2 = QRect(toggle2_x + 80, row2_y + 2, 32, 16)
        self._draw_toggle(p, sw2, self.alerts_muted, self.toggle_alerts)

        # ── Sliders ──────────────────────────────────────────
        self._draw_slider_row(
            p, rect, 88,
            "Low battery", f"{self.low_battery_pct}%",
            self.low_battery_pct, 5, 50,
            lambda v: self.set_low_battery_pct(v),
            self.theme.red,
        )
        self._draw_slider_row(
            p, rect, 114,
            "Hot zone", f"{self.hotzone_w} px",
            self.hotzone_w, 180, 800,
            lambda v: self.set_hotzone_w(v),
            self.theme.blue,
        )

    def _paint_settings_colours(self, p, rect, title_pad):
        """Page 2: colour customisation. 3 rows of accent swatches —
        Visualizer, Peak Bars, Progress Bar — each click swaps the colour."""
        rows = [
            ("Visualizer",   self.viz_accent_name,        self.set_viz_accent),
            ("Peak bars",    self.viz_peak_accent_name,   self.set_viz_peak_accent),
            ("Progress",     self.progress_accent_name,   self.set_progress_accent),
        ]
        # Each row is a label on the left and a horizontal strip of colour
        # dots on the right.
        row_h = 26
        top_y = 30
        dot_d = 16
        gap = 6
        n = len(ACCENT_NAMES)

        for ri, (label, current_name, setter) in enumerate(rows):
            y = top_y + ri * row_h
            p.setPen(self.theme.text_dim)
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRect(title_pad, y, 100, dot_d + 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       label)

            dots_w = n * dot_d + (n - 1) * gap
            dots_x = rect.right() - dots_w - title_pad
            for i, accent_name in enumerate(ACCENT_NAMES):
                x = dots_x + i * (dot_d + gap)
                dot_r = QRect(x, y + 1, dot_d, dot_d)
                # The dot's actual fill = the colour that this accent name
                # resolves to on the CURRENT theme.
                color = self._accent(accent_name, accent_name)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawEllipse(dot_r)
                # Selected ring
                if accent_name == current_name:
                    p.setPen(QPen(self.theme.text, 2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawEllipse(dot_r.adjusted(-2, -2, 2, 2))
                self._buttons.append(
                    (dot_r, lambda n=accent_name, s=setter: s(n))
                )

        # Live preview bar — shows current visualizer + peak combo
        prev_y = top_y + len(rows) * row_h + 6
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, prev_y, 60, 14),
                   Qt.AlignmentFlag.AlignLeft, "PREVIEW")
        # Mini visualizer using current settings — independent of media playing
        prev_x = title_pad + 64
        prev_w = rect.width() - prev_x - title_pad
        prev_rect = QRect(prev_x, prev_y, prev_w, 18)
        self._draw_preview_viz(p, prev_rect)

    def _draw_preview_viz(self, p, rect):
        """Same look as the real visualizer but using a fixed sine pattern,
        so the user can preview their colour choice without media playing."""
        n = 18
        gap = 2
        avail = rect.width() - 4
        bar_w = max(2, (avail - (n - 1) * gap) // n)
        total_w = n * bar_w + (n - 1) * gap
        x0 = rect.x() + (rect.width() - total_w) // 2
        max_h = rect.height() - 2
        cy = rect.center().y()
        t = time.time() * 4.0
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            shape = 1.0 - abs((i - (n - 1) / 2.0) / (n / 2.0)) * 0.55
            wave = 0.45 + 0.5 * math.sin(t + i * 0.45)
            lv = max(0.1, min(1.0, wave * shape))
            color = self.viz_peak_accent() if lv > 0.65 else self.viz_accent()
            p.setBrush(color)
            h = max(2, int(max_h * lv))
            p.drawRoundedRect(x0 + i * (bar_w + gap), cy - h // 2,
                              bar_w, h, bar_w / 2, bar_w / 2)

    def _draw_toggle(self, p, rect, on, callback):
        """iOS-style toggle. Click anywhere on the rect flips it."""
        hovered = self._is_hovered_next()
        base_bg = self.theme.green if on else self.theme.bg_track
        bg = self._tint(base_bg, 0.10) if hovered else base_bg
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob_d = rect.height() - 4
        kx = (rect.right() - knob_d - 2) if on else (rect.left() + 2)
        knob = QRect(kx, rect.top() + 2, knob_d, knob_d)
        # Subtle drop shadow on the knob so it lifts off the track.
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawEllipse(knob.adjusted(0, 1, 0, 1))
        p.setBrush(self.theme.text)
        p.drawEllipse(knob)
        # Make the toggle larger to hit-test reliably
        hit = rect.adjusted(-4, -4, 4, 4)
        self._buttons.append((hit, callback))

    def _draw_slider_row(self, p, card_rect, y, label, value_text,
                         current, low, high, on_set, accent):
        title_pad = 14
        p.setPen(self.theme.text_dim)
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(QRect(title_pad, y, 80, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   label)

        bar_x = title_pad + 84
        bar_right_pad = 64
        bar_w = card_rect.width() - bar_x - bar_right_pad - title_pad
        bar_y = y + 8
        bar = QRect(bar_x, bar_y, bar_w, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.bg_track)
        p.drawRoundedRect(bar, 2, 2)

        frac = (current - low) / max(1, (high - low))
        frac = max(0.0, min(1.0, frac))
        fill_w = int(bar_w * frac)
        p.setBrush(accent)
        if fill_w > 0:
            p.drawRoundedRect(bar_x, bar_y, fill_w, 4, 2, 2)
        # Knob
        kx = bar_x + fill_w
        knob = QRect(kx - 5, bar_y - 4, 10, 12)
        p.setBrush(self.theme.text)
        p.drawRoundedRect(knob, 3, 3)

        # Click zone — click anywhere on the bar to set value
        hit = QRect(bar_x, bar_y - 8, bar_w, 22)
        # Use closure to capture range + setter; the lambda computes click
        # position when invoked.
        self._buttons.append((hit, lambda r=hit, lo=low, hi=high, s=on_set:
                              self._slider_click(r, lo, hi, s)))

        # Value label on the right
        p.setPen(self.theme.text)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        p.drawText(QRect(bar_x + bar_w + 6, y, bar_right_pad - 4, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   value_text)

    def _slider_click(self, hit_rect, low, high, setter):
        """Compute fraction from current cursor position, hand to setter."""
        pos = self.mapFromGlobal(QCursor.pos())
        x = pos.x() - hit_rect.x()
        frac = max(0.0, min(1.0, x / max(1, hit_rect.width())))
        setter(int(round(low + frac * (high - low))))

    # ── Visual helpers ─────────────────────────────────────────
    def _draw_art(self, p, rect, radius, glyph):
        if self.media.art and not self.media.art.isNull():
            # Render at the actual screen pixel density so HiDPI displays
            # don't show a blurry upscale. Without this, scaled() targets a
            # logical-pixel size and the painter then upsamples again to
            # device pixels — two stages of softening.
            dpr = self.devicePixelRatioF() or 1.0
            target_px = QSize(
                max(1, int(rect.width() * dpr)),
                max(1, int(rect.height() * dpr)),
            )
            scaled = self.media.art.scaled(
                target_px,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(dpr)
            clip = QPainterPath()
            clip.addRoundedRect(rect.x(), rect.y(),
                                rect.width(), rect.height(),
                                radius, radius)
            p.save()
            p.setClipPath(clip)
            # Center-crop the (possibly oversized) scaled pixmap into rect.
            sx = max(0, (scaled.width() / dpr - rect.width()) / 2)
            sy = max(0, (scaled.height() / dpr - rect.height()) / 2)
            src = QRect(int(sx * dpr), int(sy * dpr),
                        int(rect.width() * dpr), int(rect.height() * dpr))
            p.drawPixmap(rect, scaled, src)
            p.restore()
        else:
            p.setBrush(self.theme.bg_inner)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, radius, radius)
            p.setPen(self.theme.text_dim)
            p.setFont(QFont("Segoe UI", glyph))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")

    def _level_from_peak(self, sample, base_min=0.10, sensitivity=1.0):
        """Convert a raw 0..1 peak sample into a 0..1 bar height fraction,
        scaled relative to the rolling envelope. This is the trick that
        keeps the bars expressive across the whole song:

        - Quiet parts: small samples but small envelope too → bars are
          still mid-height, so movement is visible.
        - Loud parts: bigger envelope, so peaks don't peg to the top —
          they swing within the range. Big drops still hit the top, but
          most of the chorus uses the middle of the range.
        - A soft-knee compressor squishes anything above 0.85 into the
          last 0.15 of bar height, so transients show as visible pumps
          instead of flat-line clipping.

        sensitivity > 1 makes bars more reactive; < 1 calms them down.
        base_min sets the floor for "playing but quiet" bars.
        """
        # Reference: roughly 1/2 of recent loudness ceiling. With this:
        #   - sample == 0.5 * envelope  →  norm == 0.5 (mid-height bar)
        #   - sample == 1.0 * envelope  →  norm == 1.0 (full-height bar)
        # Critically, normal in-chorus peaks land in the *middle* of the
        # range, leaving room for transients (kicks, snares) to swing up.
        ref = max(0.04, self._peak_envelope)
        norm = (sample / ref) * sensitivity
        # Soft-knee compressor above 0.85 — peaks compress smoothly into
        # 0.85..1.0 instead of clipping to a flat top.
        if norm > 0.85:
            over = norm - 0.85
            norm = 0.85 + (1.0 - math.exp(-over * 1.4)) * 0.15
        return max(base_min, min(1.0, norm))

    def _draw_eq(self, p, rect):
        """Mini pill-side visualizer: 3 bars driven by recent peak history."""
        bars, bar_w, gap = 3, 3, 2
        total = bars * bar_w + (bars - 1) * gap
        x0 = rect.x() + (rect.width() - total) // 2
        cy = rect.center().y()
        max_h = rect.height() - 8
        playing = self.media.is_playing
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.viz_accent() if playing else self.theme.red)

        # Sample 3 recent peaks at staggered offsets so the bars look
        # independent rather than identical. Use the RAW buffer — the
        # smoothed one kills variance during sustained chorus sections.
        n = len(self._peak_history_raw)
        levels = []
        for i in range(bars):
            offset = i * 2 + 1
            sample = self._peak_history_raw[(self._peak_idx - offset) % n]
            # Envelope-relative level so big beats don't max out — they
            # swing within the bar's range and stay visibly distinct.
            levels.append(self._level_from_peak(
                sample, base_min=0.18, sensitivity=1.0))

        if not playing:
            levels = [0.18] * bars
        for i, lv in enumerate(levels):
            h = max(2, int(max_h * lv))
            p.drawRoundedRect(x0 + i * (bar_w + gap), cy - h // 2,
                              bar_w, h, 1.5, 1.5)

    def _draw_visualizer(self, p, rect):
        """Full audio visualizer for the media card. Many bars across the
        whole rect, driven by the peak ring buffer. When no real meter is
        available, falls back to a sine-driven idle animation so the card
        still looks alive."""
        n = 28
        gap = 2
        avail_w = rect.width() - 8
        bar_w = max(2, (avail_w - (n - 1) * gap) // n)
        total_w = n * bar_w + (n - 1) * gap
        x0 = rect.x() + (rect.width() - total_w) // 2
        max_h = rect.height() - 4
        cy = rect.center().y()
        playing = self.media.is_playing

        p.setPen(Qt.PenStyle.NoPen)

        hist = self._peak_history_raw
        hn = len(hist)
        t = time.time() * 6.0
        has_real = self.audio_ctrl.get_peak() is not None if hasattr(
            self, "audio_ctrl") else False

        for i in range(n):
            if not playing:
                lv = 0.06
            elif has_real:
                # Use a different recent sample per bar — more spread now
                # that the buffer is 32 wide.
                sample = hist[(self._peak_idx - 1 - (i % hn)) % hn]
                # Envelope-relative scaling — same formula as the mini EQ.
                base = self._level_from_peak(
                    sample, base_min=0.08, sensitivity=1.15)
                # Per-bar shape: emphasise center bars (like a real spectrum)
                shape = 1.0 - abs((i - (n - 1) / 2.0) / (n / 2.0)) * 0.45
                # Per-bar jitter so neighbours don't move in lockstep.
                jitter = 0.7 + 0.3 * math.sin(t + i * 0.85)
                lv = max(0.08, min(1.0, base * shape * jitter))
            else:
                # Fallback (Mac, Linux, no pycaw): sine-based idle anim,
                # still informative because it stops on pause.
                shape = 1.0 - abs((i - (n - 1) / 2.0) / (n / 2.0)) * 0.55
                wave = 0.4 + 0.5 * math.sin(t * 0.6 + i * 0.45)
                lv = max(0.08, min(1.0, wave * shape))

            h = max(2, int(max_h * lv))
            # Gradient effect: louder bars get the peak accent
            color = self.viz_accent() if playing else self.theme.text_faint
            if playing and lv > 0.65:
                color = self.viz_peak_accent()
            p.setBrush(color)
            p.drawRoundedRect(x0 + i * (bar_w + gap),
                              cy - h // 2, bar_w, h,
                              bar_w / 2, bar_w / 2)

    def _draw_speaker_icon(self, p, rect, color, muted, level, anim,
                           animate_strike):
        """Speaker glyph + curved 'sound waves' that scale with level.
        When muted, draws a slash through it. When `animate_strike` is True,
        the slash sweeps in over the lifetime of the pill (anim ∈ [0,1])."""
        cx, cy = rect.center().x(), rect.center().y()
        s = min(rect.width(), rect.height()) * 0.36

        # Speaker body (trapezoid + box)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        body = QPolygon([
            QPoint(int(cx - s * 0.9), int(cy - s * 0.35)),
            QPoint(int(cx - s * 0.3), int(cy - s * 0.35)),
            QPoint(int(cx + s * 0.15), int(cy - s * 0.85)),
            QPoint(int(cx + s * 0.15), int(cy + s * 0.85)),
            QPoint(int(cx - s * 0.3), int(cy + s * 0.35)),
            QPoint(int(cx - s * 0.9), int(cy + s * 0.35)),
        ])
        p.drawPolygon(body)

        # Sound waves — number of arcs scales with level (if not muted)
        if not muted:
            wave_count = 3 if level > 0.66 else 2 if level > 0.33 else 1 if level > 0.02 else 0
            pen = QPen(color, max(1, int(s * 0.13)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(wave_count):
                r = s * (0.45 + 0.32 * (i + 1))
                arc_rect = QRect(int(cx + s * 0.22 - r),
                                 int(cy - r),
                                 int(2 * r), int(2 * r))
                # Pulse the outermost wave with the animation phase
                if i == wave_count - 1 and animate_strike is False and anim < 1.0:
                    pen2 = QPen(color, max(1, int(s * 0.13)))
                    pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
                    pen2.setColor(QColor(color.red(), color.green(), color.blue(),
                                         int(255 * (0.4 + 0.6 * anim))))
                    p.setPen(pen2)
                # Qt arc angles are 1/16ths of a degree.
                # Draw a 90° arc on the right, centred on the speaker mouth.
                p.drawArc(arc_rect, -45 * 16, 90 * 16)
                p.setPen(pen)
        else:
            # Mute slash
            slash_progress = anim if animate_strike else 1.0
            pen = QPen(color, max(2, int(s * 0.18)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            x1 = int(cx - s)
            y1 = int(cy - s)
            x2_full = int(cx + s)
            y2_full = int(cy + s)
            x2 = int(x1 + (x2_full - x1) * slash_progress)
            y2 = int(y1 + (y2_full - y1) * slash_progress)
            p.drawLine(x1, y1, x2, y2)

    def _draw_paperclip(self, p, rect, color):
        cx, cy = rect.center().x(), rect.center().y()
        s = min(rect.width(), rect.height()) * 0.4
        pen = QPen(color, max(2, int(s * 0.18)))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Outer hook
        outer = QRect(int(cx - s * 0.5), int(cy - s),
                      int(s), int(2 * s))
        p.drawArc(outer, 0 * 16, 180 * 16)
        # Two vertical stems
        p.drawLine(int(cx - s * 0.5), int(cy - s),
                   int(cx - s * 0.5), int(cy + s * 0.4))
        p.drawLine(int(cx + s * 0.5), int(cy - s),
                   int(cx + s * 0.5), int(cy + s * 0.6))
        # Bottom curl
        bot = QRect(int(cx - s * 0.5), int(cy + s * 0.0),
                    int(s), int(s))
        p.drawArc(bot, 180 * 16, 180 * 16)

    def _draw_file_icon(self, p, rect, color):
        # Simple folded-corner page glyph
        p.setPen(QPen(color, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        w = rect.width()
        h = rect.height()
        x = rect.x()
        y = rect.y()
        fold = max(3, int(w * 0.3))
        # Page outline
        body = QPolygon([
            QPoint(x, y),
            QPoint(x + w - fold, y),
            QPoint(x + w, y + fold),
            QPoint(x + w, y + h),
            QPoint(x, y + h),
        ])
        p.drawPolygon(body)
        # Fold corner
        p.drawLine(x + w - fold, y, x + w - fold, y + fold)
        p.drawLine(x + w - fold, y + fold, x + w, y + fold)

    def _is_hovered_next(self):
        """True if the button we're about to register will be the hovered
        one. Has to be called BEFORE _buttons.append(...) inside a drawer."""
        return self._hovered_idx == len(self._buttons)

    @staticmethod
    def _tint(color, amount):
        """Lighten (positive) or darken (negative) a QColor by `amount` 0..1.
        Used for hover/pressed states without recomputing palette colors."""
        h, s, l, a = color.getHsl()
        if amount >= 0:
            l = min(255, int(l + (255 - l) * amount))
        else:
            l = max(0, int(l + l * amount))
        c = QColor()
        c.setHsl(h, s, l, a)
        return c

    def _draw_x_button(self, p, rect, color, callback):
        hovered = self._is_hovered_next()
        bg = self._tint(self.theme.bg_inner, 0.20) if hovered else self.theme.bg_inner
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(rect)
        pen_color = self._tint(color, 0.25) if hovered else color
        pen = QPen(pen_color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        pad = rect.width() // 4
        r = rect.adjusted(pad, pad, -pad, -pad)
        p.drawLine(r.topLeft(), r.bottomRight())
        p.drawLine(r.topRight(), r.bottomLeft())
        self._buttons.append((rect, callback))

    def _draw_button(self, p, rect, glyph_drawer, callback, filled=False):
        hovered = self._is_hovered_next()
        if filled:
            bg = self._tint(self.theme.text, -0.10) if hovered else self.theme.text
        else:
            bg = self._tint(self.theme.bg_inner, 0.18) if hovered else self.theme.bg_inner
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(rect)
        glyph_drawer(p, rect, filled, theme=self.theme)
        self._buttons.append((rect, callback))

    def _draw_label_button(self, p, rect, text, color, callback):
        hovered = self._is_hovered_next()
        # On hover: brighten bg slightly, slightly thicker outline to suggest
        # a clickable surface. Same idea as macOS sidebar buttons.
        bg = self._tint(self.theme.bg_inner, 0.18) if hovered else self.theme.bg_inner
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, 8, 8)
        if hovered:
            # Subtle 1px ring
            p.setPen(QPen(self._tint(color, 0.10), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 8, 8)
        p.setPen(color)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        self._buttons.append((rect, callback))

    def _draw_chip(self, p, rect, text, color, callback):
        hovered = self._is_hovered_next()
        bg = self._tint(self.theme.bg_inner, 0.22) if hovered else self.theme.bg_inner
        p.setPen(QPen(self._tint(self.theme.border, 0.4) if hovered else self.theme.border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 11, 11)
        p.setPen(color)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        self._buttons.append((rect, callback))

    @staticmethod
    def _fmt_secs(secs):
        secs = max(0, int(secs))
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    @staticmethod
    def _fmt_hm(secs):
        secs = max(0, int(secs))
        h, rem = divmod(secs, 3600)
        m = rem // 60
        if h:
            return f"{h}h {m:02d}m"
        return f"{m}m"

    # ────────────────────────────────────────────────────────────
    #   Drag-OUT  (files held → other apps)
    # ────────────────────────────────────────────────────────────
    def _mime_for_files(self, paths):
        md = QMimeData()
        urls = [QUrl.fromLocalFile(p) for p in paths if p]
        md.setUrls(urls)
        return md

    def _start_drag_out(self, mime_factory, hotspot):
        md = mime_factory()
        if md is None:
            return
        drag = QDrag(self)
        drag.setMimeData(md)
        # Build a small drag pixmap so the user sees what they're carrying
        urls = md.urls()
        n = len(urls)
        pm_w, pm_h = 200, 36
        pm = QPixmap(pm_w, pm_h)
        pm.fill(Qt.GlobalColor.transparent)
        pp = QPainter(pm)
        pp.setRenderHint(QPainter.RenderHint.Antialiasing)
        pp.setPen(Qt.PenStyle.NoPen)
        pp.setBrush(self.theme.bg)
        pp.drawRoundedRect(0, 0, pm_w, pm_h, 18, 18)
        icon_r = QRect(8, 9, 18, 18)
        self._draw_file_icon(pp, icon_r, self.theme.green)
        pp.setPen(self.theme.text)
        pp.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        if n == 1:
            name = os.path.basename(urls[0].toLocalFile())
            fm = QFontMetrics(pp.font())
            name = fm.elidedText(name, Qt.TextElideMode.ElideMiddle, pm_w - 36)
            pp.drawText(QRect(34, 0, pm_w - 40, pm_h),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        name)
        else:
            pp.drawText(QRect(34, 0, pm_w - 40, pm_h),
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                        f"{n} files")
        pp.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(hotspot.x(), hotspot.y()))
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                  Qt.DropAction.CopyAction)
        # Drag finished — make sure we end up in a sane state
        self.update()

    # ────────────────────────────────────────────────────────────
    #   Mouse / wheel
    # ────────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            # First, did the press start inside a draggable zone?
            for rect, factory in self._drag_zones:
                if rect.contains(ev.pos()):
                    # Remember it; we'll fire the drag in mouseMoveEvent
                    # if the user moves more than ~6 px.
                    self._press_drag = (ev.pos(), factory, ev.pos())
                    return
            # Otherwise, look for buttons.
            for rect, cb in self._buttons:
                if rect.contains(ev.pos()):
                    try:
                        cb()
                    except Exception:
                        pass
                    self.update()
                    return
        elif ev.button() == Qt.MouseButton.RightButton:
            self.show_menu(ev.globalPosition().toPoint())

    def mouseMoveEvent(self, ev):
        # Update hover position so the next paint can highlight whatever
        # control is under the cursor. We do the actual hit-test against
        # `_buttons` here (cheap) and only force a repaint if the result
        # changed, so we're not redrawing on every pixel of mouse movement.
        new_pos = ev.pos()
        new_idx = -1
        for i, (rect, _cb) in enumerate(self._buttons):
            if rect.contains(new_pos):
                new_idx = i
                break
        if new_idx != self._hovered_idx:
            self._hovered_idx = new_idx
            self._hover_pos = new_pos
            self.update()
        else:
            self._hover_pos = new_pos

        # If a press began inside a drag zone, start the drag once movement
        # exceeds the threshold. After this point control belongs to QDrag
        # until the user drops — Qt re-enters this widget via dragEnter etc.
        if self._press_drag is not None:
            start_pos, factory, hotspot = self._press_drag
            if (ev.pos() - start_pos).manhattanLength() > 6:
                pd = self._press_drag
                self._press_drag = None
                self._start_drag_out(pd[1], pd[2])
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        # Drop hover state when the cursor leaves the widget entirely.
        if self._hovered_idx != -1:
            self._hovered_idx = -1
            self._hover_pos = None
            self.update()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev):
        self._press_drag = None
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev):
        if self.state != State.EXPAND:
            return
        delta = ev.angleDelta().y()
        if delta == 0:
            return

        # Volume zone: scrolling DIRECTLY ON the volume slider adjusts
        # volume. Scrolling anywhere else on the card switches cards
        # like normal — so the user is never trapped on a card.
        pos = ev.position().toPoint()
        if (self._effective_card() == Card.AUDIO
                and self.audio_ctrl.available
                and self._volume_track_rect is not None
                and self._volume_track_rect.contains(pos)):
            step = VOLUME_STEP if delta > 0 else -VOLUME_STEP
            self.step_volume(step)
            self.update()
            return

        # Reset accumulator if direction reversed
        if (delta > 0) != (self._scroll_accum >= 0):
            self._scroll_accum = 0
        self._scroll_accum += delta
        threshold = 120
        if abs(self._scroll_accum) >= threshold:
            direction = 1 if self._scroll_accum > 0 else -1
            self._scroll_accum = 0
            cur = self._effective_card()
            try:
                i = CARDS_ORDER.index(cur)
            except ValueError:
                i = 1
            i = (i - direction) % len(CARDS_ORDER)
            self.card = CARDS_ORDER[i]
            self.update()

    # ────────────────────────────────────────────────────────────
    #   Context menu
    # ────────────────────────────────────────────────────────────
    def show_menu(self, global_pos):
        m = QMenu()
        m.addAction("Show Now", lambda: self.manual_summon(4))
        m.addAction("Pin / Unpin", self.toggle_pin)
        if self.pinned:
            m.addAction("    ✓  Pinned").setEnabled(False)
        m.addSeparator()
        # Hide entirely. Once chosen, the only way back is the tray menu.
        m.addAction("Hide Island  (re-enable from tray)",
                    lambda: self.set_disabled(True))
        m.addSeparator()

        # ── Theme submenu ────────────────────────────────────
        theme_menu = m.addMenu(f"Theme  ·  {self.theme.label}")
        for name in THEME_ORDER:
            t = THEMES[name]
            label = ("✓  " if name == self.theme.name else "      ") + t.label
            theme_menu.addAction(label, lambda n=name: self.set_theme(n))

        # ── Volume submenu ───────────────────────────────────
        vol = m.addMenu("Volume")
        if self.audio_ctrl.available:
            cur = self.audio.volume
            cur_label = (f"  Current  ·  {int(round(cur * 100))}%"
                         if cur is not None else "  Current  ·  ?")
            vol.addAction(cur_label).setEnabled(False)
            vol.addSeparator()
            for v in (0.0, 0.25, 0.5, 0.75, 1.0):
                vol.addAction(f"Set to {int(v * 100)}%",
                              lambda f=v: self.set_volume_frac(f))
            vol.addSeparator()
        vol.addAction("Volume Up   (+5%)",
                      lambda: self.step_volume(VOLUME_STEP))
        vol.addAction("Volume Down (−5%)",
                      lambda: self.step_volume(-VOLUME_STEP))
        vol.addAction(("Unmute" if self.audio.muted else "Mute") + " System",
                      self.toggle_system_mute)

        m.addSeparator()
        # ── Files ───────────────────────────────────────────
        if self.files:
            files_menu = m.addMenu(f"Held Files  ·  {len(self.files)}")
            for i, f in enumerate(list(self.files)):
                name = f.name if len(f.name) <= 40 else f.name[:37] + "…"
                files_menu.addAction(
                    f"Remove  ·  {name}",
                    lambda idx=i: self.remove_file(idx),
                )
            files_menu.addSeparator()
            files_menu.addAction("Clear All", self.clear_files)
        m.addSeparator()
        m.addAction("Add Event…", self.add_event_dialog)
        if self.events:
            m.addAction(f"Clear All Events  ({len(self.events)})", self.clear_events)
        m.addSeparator()
        m.addAction("Add Note…", self.add_note_dialog)
        if self.notes:
            m.addAction(f"Clear All Notes  ({len(self.notes)})", self.clear_notes)
        m.addSeparator()
        m.addAction("Start Timer…", self.prompt_timer)
        if self._timer_active():
            m.addAction("Cancel Timer", self.cancel_timer)
        if self.stopwatch.is_active():
            m.addAction(
                "Pause Stopwatch" if self.stopwatch.running else "Resume Stopwatch",
                self.stopwatch.toggle,
            )
            m.addAction("Reset Stopwatch", self.stopwatch.reset)
        else:
            m.addAction("Start Stopwatch", self.stopwatch.start)
        m.addSeparator()
        m.addAction("Unmute Alerts" if self.alerts_muted else "Mute Alerts",
                    self.toggle_alerts)
        m.addSeparator()
        m.addAction("Quit", QApplication.instance().quit)
        m.exec(global_pos)

    def prompt_timer(self):
        text, ok = QInputDialog.getText(
            self, "Start Timer",
            "Duration  (e.g.  90s   ·   5m   ·   1h30m)",
        )
        if not ok or not text:
            return
        secs = parse_duration(text)
        if secs > 0:
            self.start_timer(secs)


# ─────────────────────────────────────────────────────────────────────
#   Glyph drawers for media buttons
# ─────────────────────────────────────────────────────────────────────
def _glyph_color(filled, theme):
    return theme.bg if filled else theme.text


def _draw_play(p, rect, filled=False, theme=None):
    cx, cy = rect.center().x(), rect.center().y()
    s = rect.width() * 0.22
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled, theme or THEMES["classic"]))
    p.drawPolygon(QPolygon([
        QPoint(int(cx - s * 0.8), int(cy - s)),
        QPoint(int(cx - s * 0.8), int(cy + s)),
        QPoint(int(cx + s),       int(cy)),
    ]))


def _draw_pause(p, rect, filled=False, theme=None):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled, theme or THEMES["classic"]))
    bw = rect.width() * 0.14
    gap = rect.width() * 0.14
    h = rect.height() * 0.45
    cx, cy = rect.center().x(), rect.center().y()
    p.drawRoundedRect(int(cx - gap / 2 - bw), int(cy - h / 2),
                      int(bw), int(h), 2, 2)
    p.drawRoundedRect(int(cx + gap / 2), int(cy - h / 2),
                      int(bw), int(h), 2, 2)


def _draw_next(p, rect, filled=False, theme=None):
    cx, cy = rect.center().x(), rect.center().y()
    s = rect.width() * 0.20
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled, theme or THEMES["classic"]))
    p.drawPolygon(QPolygon([
        QPoint(int(cx - s - 2), int(cy - s)),
        QPoint(int(cx - s - 2), int(cy + s)),
        QPoint(int(cx + 2),     int(cy)),
    ]))
    p.drawRoundedRect(int(cx + 4), int(cy - s), 3, int(2 * s), 1, 1)


def _draw_prev(p, rect, filled=False, theme=None):
    cx, cy = rect.center().x(), rect.center().y()
    s = rect.width() * 0.20
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled, theme or THEMES["classic"]))
    p.drawRoundedRect(int(cx - s - 4), int(cy - s), 3, int(2 * s), 1, 1)
    p.drawPolygon(QPolygon([
        QPoint(int(cx + s + 2), int(cy - s)),
        QPoint(int(cx + s + 2), int(cy + s)),
        QPoint(int(cx - 2),     int(cy)),
    ]))


# ─────────────────────────────────────────────────────────────────────
#   Misc helpers
# ─────────────────────────────────────────────────────────────────────
def parse_duration(s):
    s = s.strip().lower().replace(" ", "")
    if s.isdigit():
        return int(s)
    total, num = 0, ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif num:
            n = int(num)
            if ch == "h":   total += n * 3600
            elif ch == "m": total += n * 60
            elif ch == "s": total += n
            num = ""
    if num:
        total += int(num)
    return total


def _fmt_bps(bps):
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.1f} MB/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} KB/s"
    return f"{int(bps)} B/s"


def make_tray_icon():
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0))
    p.drawRoundedRect(2, 11, 28, 10, 5, 5)
    p.end()
    return QIcon(pm)


# ─────────────────────────────────────────────────────────────────────
#   Entry point
# ─────────────────────────────────────────────────────────────────────
def main():
    # Use the OS-reported device pixel ratio when scaling pixmaps so HiDPI
    # displays render the cover art crisp, not blurry. Has to be set BEFORE
    # the QApplication is constructed, because by then high-dpi policy is
    # locked in. (Qt 6 already enables HiDPI by default, but we make it
    # explicit to be safe across versions.)
    try:
        from PyQt6.QtCore import Qt as _Qt
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            _Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    island = DynamicIsland()

    # Tray
    tray = QSystemTrayIcon(make_tray_icon())
    tray.setToolTip("Dynamic Island")
    menu = QMenu()

    # Rebuild the menu every time it opens, so the Show/Hide toggle label
    # tracks the current disabled flag and the theme submenu's checkmark
    # follows the active theme.
    def _build_tray_menu():
        menu.clear()

        # ── Show / Hide toggle (always at the top) ───────────
        if island.disabled:
            menu.addAction("✓  Hidden  —  click to show",
                           lambda: island.set_disabled(False))
            menu.addSeparator()
            menu.addAction("Quit", app.quit)
            return

        menu.addAction("Hide Island", lambda: island.set_disabled(True))
        menu.addSeparator()
        menu.addAction("Show Now", lambda: island.manual_summon(4))
        menu.addAction("Pin / Unpin", island.toggle_pin)
        menu.addSeparator()

        # Theme submenu
        theme_menu = menu.addMenu(f"Theme  ·  {island.theme.label}")
        for name in THEME_ORDER:
            t = THEMES[name]
            label = ("✓  " if name == island.theme.name else "      ") + t.label
            theme_menu.addAction(label, lambda n=name: island.set_theme(n))

        # Volume submenu
        vol_menu = menu.addMenu("Volume")
        vol_menu.addAction("Volume Up   (+5%)",
                           lambda: island.step_volume(VOLUME_STEP))
        vol_menu.addAction("Volume Down (−5%)",
                           lambda: island.step_volume(-VOLUME_STEP))
        vol_menu.addAction("Toggle Mute", island.toggle_system_mute)

        menu.addSeparator()
        menu.addAction("Add Event…", island.add_event_dialog)
        menu.addAction("Add Note…", island.add_note_dialog)
        menu.addAction("Start Timer…", island.prompt_timer)
        sw = menu.addMenu("Stopwatch")
        sw.addAction("Start / Pause", island.stopwatch.toggle)
        sw.addAction("Reset", island.stopwatch.reset)
        menu.addSeparator()
        menu.addAction("Mute / Unmute Alerts", island.toggle_alerts)
        menu.addSeparator()
        menu.addAction("Quit", app.quit)

    _build_tray_menu()
    menu.aboutToShow.connect(_build_tray_menu)
    tray.setContextMenu(menu)

    # On Windows, double-clicking the tray icon is a useful way to toggle
    # show/hide without opening the menu first.
    def _on_tray_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            island.toggle_disabled()
    tray.activated.connect(_on_tray_activated)

    tray.show()

    # Global hotkey  (best-effort)
    bridge = HotkeyBridge()
    bridge.triggered.connect(lambda: island.manual_summon(4))
    if HAS_HOTKEY:
        try:
            kb.add_hotkey("ctrl+shift+space", bridge.triggered.emit)
        except Exception as e:
            print(f"[hotkey] could not register Ctrl+Shift+Space: {e}")

    def _cleanup():
        island.media_poller.requestInterruption()
        island.sys_poller.requestInterruption()
        island.weather_poller.requestInterruption()
        island.audio_poller.requestInterruption()
        island.media_poller.wait(1500)
        island.sys_poller.wait(1500)
        island.weather_poller.wait(1500)
        island.audio_poller.wait(1500)

    app.aboutToQuit.connect(_cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
