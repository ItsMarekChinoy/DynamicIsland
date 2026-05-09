#!/usr/bin/env python3
"""
Dynamic Island for Laptop  v3
=============================

Single-file iPhone Dynamic Island clone for the top of your laptop screen.

Triggered by hovering the top-middle edge of the screen, near your camera.

NEW IN v3
---------
• Clipboard pill — pops a preview when you copy text
• Sound alerts on timer end and pomodoro phase changes (mutable)
• Quick notes — jot a note from the menu, view recent ones in a Notes card
• Timer presets — tap 5m / 15m / 30m chips to start instantly
• Battery time remaining shown alongside the percentage
• Network speeds (↑ / ↓) on the system card
• Pin mode — keep the island expanded as long as you want
• Battery row turns red under the low-battery threshold

ALREADY IN v2
-------------
Media controls (play / pause / skip), live progress, pomodoro, stopwatch,
weather card, system stats, scroll-wheel card switcher, global hotkey
(Ctrl+Shift+Space), auto-hide on fullscreen.

QUICK START
-----------
    python dynamic_island.py

First run pip-installs PyQt6, psutil, (winsdk on Windows, keyboard if available).

PLATFORMS
---------
Windows : full media + album art + transport controls via SMTC.
macOS   : Spotify / Apple Music titles & control via AppleScript.
Linux   : MPRIS via `playerctl`  (install with package manager).

TUNE
----
Edit constants in the "Tunables" section — sizes, hot zone, pomodoro length,
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
    _ensure("keyboard")
    import keyboard as kb  # noqa: F401
    HAS_HOTKEY = True
except Exception:
    HAS_HOTKEY = False


# ─────────────────────────────────────────────────────────────────────
#   Imports
# ─────────────────────────────────────────────────────────────────────
import asyncio
import json
import math
import os
import time
import urllib.request
from datetime import datetime
from enum import Enum

import psutil

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
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
PILL_W, PILL_H  = 360, 40
EXPAND_W, EXPAND_H = 540, 160
TOP_MARGIN      = 6

ANIM_MS         = 380
HOVER_POLL_MS   = 50
MEDIA_POLL_MS   = 1500
SYSTEM_POLL_MS  = 1500
FULLSCREEN_POLL_MS = 1500
WEATHER_REFRESH_S = 30 * 60
HIDE_DELAY_MS   = 800
NOTIF_DURATION_S = 4
LOW_BATTERY_PCT = 20
CLIP_PREVIEW_LEN = 50
CLIP_PILL_DURATION = 2.5
NOTES_PATH = os.path.expanduser("~/.dynamic_island_notes.json")
NOTES_VISIBLE = 3
NOTES_MAX = 30

POMO_WORK_MIN   = 25
POMO_BREAK_MIN  = 5

ACCENT_AMBER    = QColor(255, 159, 10)
ACCENT_GREEN    = QColor(120, 220, 120)
ACCENT_RED      = QColor(255, 90, 90)
ACCENT_BLUE     = QColor(80, 160, 255)
ACCENT_PURPLE   = QColor(180, 130, 255)
TEXT_PRIMARY    = QColor(255, 255, 255)
TEXT_DIM        = QColor(170, 170, 170)
TEXT_FAINT      = QColor(120, 120, 120)


class State(Enum):
    HIDDEN = 0
    PILL   = 1
    EXPAND = 2


class Card(Enum):
    AUTO   = 0   # smart pick (media if playing, else clock)
    MEDIA  = 1
    CLOCK  = 2
    SYSTEM = 3
    TIMERS = 4
    NOTES  = 5


CARDS_ORDER = [Card.MEDIA, Card.CLOCK, Card.SYSTEM, Card.TIMERS, Card.NOTES]


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


class Notification:
    __slots__ = ("kind", "text", "accent", "expires_at")

    def __init__(self, kind, text, accent, duration_s=NOTIF_DURATION_S):
        self.kind = kind
        self.text = text
        self.accent = accent
        self.expires_at = time.time() + duration_s

    def alive(self):
        return time.time() < self.expires_at


# ─────────────────────────────────────────────────────────────────────
#   Pomodoro + Stopwatch
# ─────────────────────────────────────────────────────────────────────
class Pomodoro:
    def __init__(self):
        self.active = False
        self.phase = "work"
        self.phase_end = 0.0
        self.cycle = 0
        self.work_min = POMO_WORK_MIN
        self.break_min = POMO_BREAK_MIN

    def start(self, work_min=POMO_WORK_MIN, break_min=POMO_BREAK_MIN):
        self.work_min = work_min
        self.break_min = break_min
        self.phase = "work"
        self.phase_end = time.time() + work_min * 60
        self.cycle = 1
        self.active = True

    def stop(self):
        self.active = False

    def tick(self):
        if not self.active:
            return None
        if time.time() >= self.phase_end:
            if self.phase == "work":
                self.phase = "break"
                self.phase_end = time.time() + self.break_min * 60
                return "break"
            else:
                self.phase = "work"
                self.phase_end = time.time() + self.work_min * 60
                self.cycle += 1
                return "work"
        return None

    def remaining(self):
        return max(0, int(self.phase_end - time.time()))


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
#   Weather
# ─────────────────────────────────────────────────────────────────────
def fetch_weather():
    try:
        req = urllib.request.Request(
            "https://wttr.in/?format=j1",
            headers={"User-Agent": "DynamicIslandLaptop/3.0"},
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


# ─────────────────────────────────────────────────────────────────────
#   Fullscreen detection (Windows)
# ─────────────────────────────────────────────────────────────────────
def is_fullscreen_active():
    """True only for genuinely fullscreen apps (videos, games), NOT maximised windows.
    A real fullscreen window has no title bar (no WS_CAPTION style)."""
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
        # Crucial: also check there's no title bar. Maximised windows have
        # WS_CAPTION; true fullscreen apps (video players, games) don't.
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000  # resizable border = not fullscreen
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

        # ── Core state ──────────────────────────────────────────
        self.state = State.HIDDEN
        self.card = Card.AUTO
        self.media = MediaInfo()
        self.weather = WeatherInfo()
        self.sysinfo = SystemInfo()
        self.timer_end = None
        self.stopwatch = Stopwatch()
        self.pomodoro = Pomodoro()
        self.notif = None
        self.notes = load_notes()

        # ── Toggles & mode flags ────────────────────────────────
        self.pinned = False
        self.alerts_muted = False

        # ── Ephemeral / detection state ─────────────────────────
        self._fullscreen = False
        self.hovering = False
        self._buttons = []
        self._prev_battery_plugged = None
        self._prev_low_battery = False
        self._manual_summon_until = 0.0
        self._last_clip = ""
        self._scroll_accum = 0

        scr = QGuiApplication.primaryScreen().geometry()
        self.screen_w = scr.width()
        self.screen_y = scr.y()

        self.setGeometry(self._geom_for(State.HIDDEN))
        self.show()

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

        # Enforce always-on-top every few seconds (Windows can push us down)
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

        # ── Clipboard watcher (NEW) ─────────────────────────────
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._on_clipboard_change)
        self._clipboard = clipboard

    # ────────────────────────────────────────────────────────────
    #   Geometry / state
    # ────────────────────────────────────────────────────────────
    def _geom_for(self, state):
        if state == State.HIDDEN:
            w, h = 4, 1
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
        if state == self.state:
            return
        self.state = state
        if state == State.HIDDEN:
            self.setGeometry(self._geom_for(state))
        else:
            self._animate_to(self._geom_for(state))
            self.raise_()
            self._force_topmost()
        self.update()

    def _force_topmost(self):
        """Aggressively re-assert always-on-top using Windows API.
        Qt's WindowStaysOnTopHint isn't always honoured when other apps
        explicitly raise themselves. SetWindowPos(HWND_TOPMOST) fixes it."""
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
        new_phase = self.pomodoro.tick()
        if new_phase == "work":
            self._notify("pomo",
                         f"Work {self.pomodoro.work_min}m  · cycle {self.pomodoro.cycle}",
                         ACCENT_RED)
            self._beep()
        elif new_phase == "break":
            self._notify("pomo",
                         f"Break {self.pomodoro.break_min}m  · take 5",
                         ACCENT_GREEN)
            self._beep()

        if self.timer_end is not None and time.time() >= self.timer_end:
            self.timer_end = None
            self._notify("timer", "Timer finished", ACCENT_AMBER, duration_s=6)
            self._beep()

        if self.notif is not None and not self.notif.alive():
            self.notif = None
            self._collapse_to_resting()

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
            return  # keep current state when pinned
        if self._fullscreen and time.time() > self._manual_summon_until:
            return
        pos = QCursor.pos()
        cx = self.screen_w // 2
        in_x = abs(pos.x() - cx) <= HOTZONE_W // 2
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
        if self.hovering or self.pinned:
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
            or self.pomodoro.active
            or self.media.is_active()
        )

    # ────────────────────────────────────────────────────────────
    #   Fullscreen
    # ────────────────────────────────────────────────────────────
    def _poll_fullscreen(self):
        if self.pinned:
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
        if self.hovering or self._timer_active() or self.pomodoro.active or self.pinned:
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
                self._notify("charge", f"Charging  · {info.battery_pct}%", ACCENT_GREEN)
            else:
                self._notify("unplug", f"On battery  · {info.battery_pct}%", TEXT_DIM)
        is_low = (info.battery_pct is not None
                  and info.battery_pct <= LOW_BATTERY_PCT
                  and not info.battery_plugged)
        if is_low and not self._prev_low_battery:
            self._notify("low_batt", f"Battery low  · {info.battery_pct}%",
                         ACCENT_RED, duration_s=6)
            self._beep()
        self._prev_low_battery = is_low
        self._prev_battery_plugged = info.battery_plugged
        self.sysinfo = info

    def _on_weather(self, info):
        self.weather = info

    # ────────────────────────────────────────────────────────────
    #   Clipboard watcher (NEW)
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
        self._notify("clip", f"Copied  · {preview}", ACCENT_PURPLE,
                     duration_s=CLIP_PILL_DURATION)

    # ────────────────────────────────────────────────────────────
    #   Notifications
    # ────────────────────────────────────────────────────────────
    def _notify(self, kind, text, accent, duration_s=NOTIF_DURATION_S):
        self.notif = Notification(kind, text, accent, duration_s)
        if not self.hovering and not self.pinned:
            self._set_state(State.PILL)

    def _notif_alive(self):
        return self.notif is not None and self.notif.alive()

    # ────────────────────────────────────────────────────────────
    #   Public ops
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

    def start_pomodoro(self, work=POMO_WORK_MIN, br=POMO_BREAK_MIN):
        self.pomodoro.start(work, br)
        self._notify("pomo", f"Pomodoro  · work {work}m", ACCENT_RED)

    def stop_pomodoro(self):
        self.pomodoro.stop()
        self._collapse_to_resting()

    def manual_summon(self, seconds=4):
        self._manual_summon_until = time.time() + seconds
        self._set_state(State.EXPAND)
        # Schedule a collapse after the hold period ends, unless mouse is in zone
        QTimer.singleShot(int(seconds * 1000) + 100,
                          self._maybe_collapse_after_summon)

    def _maybe_collapse_after_summon(self):
        if self.pinned or self.hovering:
            return
        # Check if cursor entered the hot zone during the hold
        pos = QCursor.pos()
        cx = self.screen_w // 2
        in_x = abs(pos.x() - cx) <= HOTZONE_W // 2
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
            self._notify("note", "Note saved", ACCENT_GREEN, duration_s=2)

    def clear_notes(self):
        self.notes = []
        save_notes(self.notes)

    # ────────────────────────────────────────────────────────────
    #   Painting — entry
    # ────────────────────────────────────────────────────────────
    def paintEvent(self, _ev):
        if self.state == State.HIDDEN:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        radius = min(rect.height() / 2, 28)
        path.addRoundedRect(rect.x(), rect.y(), rect.width(), rect.height(),
                            radius, radius)
        p.fillPath(path, QColor(0, 0, 0))

        self._buttons.clear()

        if self.state == State.PILL:
            self._paint_pill(p, rect)
        else:
            self._paint_expanded(p, rect)
            if self.pinned:
                self._draw_pin_indicator(p, rect)

    # ── Pill ──────────────────────────────────────────────────
    def _paint_pill(self, p, rect):
        if self._notif_alive():
            self._paint_notif_pill(p, rect)
        elif self._timer_active():
            self._paint_countdown_pill(
                p, rect,
                self._fmt_secs(int(self.timer_end - time.time())),
                ACCENT_AMBER,
            )
        elif self.pomodoro.active:
            label = "Work" if self.pomodoro.phase == "work" else "Break"
            color = ACCENT_RED if self.pomodoro.phase == "work" else ACCENT_GREEN
            self._paint_countdown_pill(
                p, rect,
                f"{label}  {self._fmt_secs(self.pomodoro.remaining())}",
                color,
            )
        elif self.media.is_active():
            self._paint_media_pill(p, rect)

    def _paint_notif_pill(self, p, rect):
        n = self.notif
        h, pad = rect.height(), 6
        dot = QRect(pad + 2, h // 2 - 4, 8, 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(n.accent)
        p.drawEllipse(dot)
        text_rect = QRect(dot.right() + 8, 0,
                          rect.width() - dot.right() - 16, h)
        p.setPen(TEXT_PRIMARY)
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Medium))
        fm = QFontMetrics(p.font())
        p.drawText(text_rect,
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(n.text, Qt.TextElideMode.ElideRight, text_rect.width()))

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
        p.setPen(TEXT_PRIMARY)
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
            p.setBrush(QColor(60, 60, 60))
            p.drawRoundedRect(bar_x, bar_y, bar_w, 2, 1, 1)
            p.setBrush(ACCENT_GREEN if self.media.is_playing else TEXT_DIM)
            p.drawRoundedRect(bar_x, bar_y, int(bar_w * frac), 2, 1, 1)

    # ── Expanded ───────────────────────────────────────────────
    def _paint_expanded(self, p, rect):
        card = self._effective_card()
        if card == Card.MEDIA:
            self._paint_media_card(p, rect)
        elif card == Card.SYSTEM:
            self._paint_system_card(p, rect)
        elif card == Card.TIMERS:
            self._paint_timers_card(p, rect)
        elif card == Card.NOTES:
            self._paint_notes_card(p, rect)
        else:
            self._paint_clock_card(p, rect)
        self._paint_card_dots(p, rect)

    def _effective_card(self):
        if self.card != Card.AUTO:
            return self.card
        if self.media.is_active():
            return Card.MEDIA
        return Card.CLOCK

    def _paint_card_dots(self, p, rect):
        active = self._effective_card()
        n = len(CARDS_ORDER)
        gap = 6
        d = 4
        total = n * d + (n - 1) * gap
        x = rect.center().x() - total // 2
        y = rect.bottom() - 12
        for c in CARDS_ORDER:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(TEXT_PRIMARY if c == active else QColor(60, 60, 60))
            p.drawEllipse(x, y, d, d)
            x += d + gap

    def _draw_pin_indicator(self, p, rect):
        # Tiny purple dot in the top-right corner.
        d = 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT_PURPLE)
        p.drawEllipse(rect.right() - d - 8, 8, d, d)

    # ── Clock + Weather card ───────────────────────────────────
    def _paint_clock_card(self, p, rect):
        now = datetime.now()
        p.setPen(TEXT_PRIMARY)
        p.setFont(QFont("Segoe UI", 30, QFont.Weight.Light))
        p.drawText(QRect(20, 18, 220, 44),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%H:%M"))
        p.setPen(TEXT_DIM)
        p.setFont(QFont("Segoe UI", 10))
        p.drawText(QRect(20, 64, 260, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%A, %B %d"))

        wx_x = rect.width() // 2 + 10
        if self.weather.is_fresh():
            p.setPen(TEXT_PRIMARY)
            p.setFont(QFont("Segoe UI", 26, QFont.Weight.Light))
            p.drawText(QRect(wx_x, 18, rect.width() - wx_x - 20, 44),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{self.weather.temp_c}°C")
            p.setPen(TEXT_DIM)
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRect(wx_x, 64, rect.width() - wx_x - 20, 22),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self.weather.desc)
        else:
            p.setPen(TEXT_FAINT)
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRect(wx_x, 34, rect.width() - wx_x - 20, 22),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "Weather offline")

        p.setPen(TEXT_FAINT)
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRect(20, rect.bottom() - 32, rect.width() - 40, 14),
                   Qt.AlignmentFlag.AlignLeft,
                   "scroll · switch cards     right-click · menu     "
                   + ("Ctrl+Shift+Space · summon" if HAS_HOTKEY else ""))

    # ── Media card ─────────────────────────────────────────────
    def _paint_media_card(self, p, rect):
        if not self.media.title:
            self._paint_clock_card(p, rect)
            return
        pad = 14
        art_size = EXPAND_H - 2 * pad - 12
        art = QRect(pad, pad, art_size, art_size)
        self._draw_art(p, art, radius=10, glyph=24)

        info_x = art.right() + 14
        info_w = rect.width() - info_x - pad
        p.setPen(TEXT_PRIMARY)
        p.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        fm = QFontMetrics(p.font())
        p.drawText(QRect(info_x, pad + 2, info_w, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.media.title, Qt.TextElideMode.ElideRight, info_w))
        p.setPen(TEXT_DIM)
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
        p.setBrush(QColor(60, 60, 60))
        p.drawRoundedRect(bar_x, bar_y, bar_w, 3, 1.5, 1.5)
        p.setBrush(ACCENT_GREEN if self.media.is_playing else TEXT_DIM)
        p.drawRoundedRect(bar_x, bar_y, int(bar_w * frac), 3, 1.5, 1.5)

        p.setPen(TEXT_FAINT)
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
        p.setPen(TEXT_PRIMARY)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, 10, rect.width() - 2 * title_pad, 20),
                   Qt.AlignmentFlag.AlignLeft, "System")

        # Build the rows
        rows = [
            ("CPU", s.cpu, ACCENT_BLUE, f"{s.cpu}%"),
            ("RAM", s.ram, ACCENT_AMBER, f"{s.ram}%"),
        ]
        if s.battery_pct is not None:
            color = ACCENT_GREEN if s.battery_pct > LOW_BATTERY_PCT else ACCENT_RED
            tag = f"{s.battery_pct}%"
            if s.battery_plugged:
                tag += "  ⚡"
            elif s.battery_secs:
                tag += f"  · {self._fmt_hm(s.battery_secs)}"
            rows.append(("Battery", s.battery_pct, color, tag))

        list_y = 34
        # Reserve last 24 px for network row.
        rows_area_h = rect.height() - list_y - 24 - 16
        row_h = rows_area_h // max(1, len(rows))
        for i, (label, pct, col, tag) in enumerate(rows):
            y = list_y + i * row_h
            p.setPen(TEXT_DIM)
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
            p.drawText(QRect(title_pad, y, 70, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       label)
            bar_x = title_pad + 76
            bar_w = rect.width() - bar_x - 130
            bar_y = y + row_h // 2 - 3
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(45, 45, 45))
            p.drawRoundedRect(bar_x, bar_y, bar_w, 6, 3, 3)
            p.setBrush(col)
            p.drawRoundedRect(bar_x, bar_y, int(bar_w * pct / 100), 6, 3, 3)
            p.setPen(TEXT_PRIMARY)
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
            p.drawText(QRect(bar_x + bar_w + 8, y, 120, row_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, tag)

        # Network row at the bottom (NEW)
        net_y = rect.height() - 42
        p.setPen(TEXT_DIM)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        p.drawText(QRect(title_pad, net_y, 70, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "Net")
        net_text = f"↑ {_fmt_bps(s.net_up_bps)}     ↓ {_fmt_bps(s.net_down_bps)}"
        p.setPen(TEXT_PRIMARY)
        p.drawText(QRect(title_pad + 76, net_y, rect.width() - title_pad - 96, 18),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   net_text)

    # ── Timers card (timer / stopwatch / pomodoro) ─────────────
    def _paint_timers_card(self, p, rect):
        cols = 3
        col_w = rect.width() // cols
        for i, drawer in enumerate((self._draw_timer_col,
                                    self._draw_stopwatch_col,
                                    self._draw_pomo_col)):
            col_rect = QRect(i * col_w, 0, col_w, rect.height() - 16)
            drawer(p, col_rect)
            if i > 0:
                p.setPen(QColor(40, 40, 40))
                p.drawLine(col_rect.x(), 16,
                           col_rect.x(), rect.height() - 28)

    def _draw_timer_col(self, p, r):
        p.setPen(TEXT_DIM)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(r.x(), r.y() + 6, r.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "TIMER")
        active = self._timer_active()
        if active:
            p.setPen(ACCENT_AMBER)
            p.setFont(QFont("Segoe UI", 22, QFont.Weight.Light))
            p.drawText(QRect(r.x(), r.y() + 26, r.width(), 36),
                       Qt.AlignmentFlag.AlignCenter,
                       self._fmt_secs(int(self.timer_end - time.time())))
            btn_w, btn_h = 90, 24
            btn = QRect(r.x() + (r.width() - btn_w) // 2,
                        r.y() + r.height() - btn_h - 4, btn_w, btn_h)
            self._draw_label_button(p, btn, "Cancel", ACCENT_AMBER, self.cancel_timer)
        else:
            # Preset chips (NEW)
            chip_y = r.y() + 32
            chip_h = 22
            chips = [("5m", 5 * 60), ("15m", 15 * 60), ("30m", 30 * 60)]
            chip_w = 38
            gap = 8
            total_w = 3 * chip_w + 2 * gap
            x0 = r.x() + (r.width() - total_w) // 2
            for i, (label, secs) in enumerate(chips):
                cr = QRect(x0 + i * (chip_w + gap), chip_y, chip_w, chip_h)
                self._draw_chip(p, cr, label, ACCENT_AMBER,
                                lambda s=secs: self.start_timer(s))

            btn_w, btn_h = 90, 24
            btn = QRect(r.x() + (r.width() - btn_w) // 2,
                        r.y() + r.height() - btn_h - 4, btn_w, btn_h)
            self._draw_label_button(p, btn, "Custom…", TEXT_PRIMARY, self.prompt_timer)

    def _draw_stopwatch_col(self, p, r):
        p.setPen(TEXT_DIM)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(r.x(), r.y() + 6, r.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "STOPWATCH")
        sw = self.stopwatch
        running = sw.running
        p.setPen(ACCENT_BLUE if running
                 else (TEXT_PRIMARY if sw.is_active() else TEXT_FAINT))
        p.setFont(QFont("Segoe UI", 22, QFont.Weight.Light))
        p.drawText(QRect(r.x(), r.y() + 26, r.width(), 36),
                   Qt.AlignmentFlag.AlignCenter,
                   self._fmt_secs(int(sw.total())))
        btn_w, btn_h = 60, 24
        gap = 6
        total = 2 * btn_w + gap
        x = r.x() + (r.width() - total) // 2
        b1 = QRect(x, r.y() + r.height() - btn_h - 4, btn_w, btn_h)
        b2 = QRect(x + btn_w + gap, r.y() + r.height() - btn_h - 4, btn_w, btn_h)
        self._draw_label_button(
            p, b1, "Pause" if running else "Start",
            ACCENT_BLUE if running else TEXT_PRIMARY,
            sw.toggle,
        )
        self._draw_label_button(p, b2, "Reset", TEXT_DIM, sw.reset)

    def _draw_pomo_col(self, p, r):
        p.setPen(TEXT_DIM)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        p.drawText(QRect(r.x(), r.y() + 6, r.width(), 16),
                   Qt.AlignmentFlag.AlignCenter, "POMODORO")
        po = self.pomodoro
        if po.active:
            color = ACCENT_RED if po.phase == "work" else ACCENT_GREEN
            label = "Work" if po.phase == "work" else "Break"
            p.setPen(color)
            p.setFont(QFont("Segoe UI", 18, QFont.Weight.Light))
            p.drawText(QRect(r.x(), r.y() + 22, r.width(), 26),
                       Qt.AlignmentFlag.AlignCenter,
                       self._fmt_secs(po.remaining()))
            p.setPen(TEXT_DIM)
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRect(r.x(), r.y() + 48, r.width(), 18),
                       Qt.AlignmentFlag.AlignCenter,
                       f"{label}  · cycle {po.cycle}")
        else:
            p.setPen(TEXT_FAINT)
            p.setFont(QFont("Segoe UI", 18, QFont.Weight.Light))
            p.drawText(QRect(r.x(), r.y() + 22, r.width(), 26),
                       Qt.AlignmentFlag.AlignCenter,
                       f"{po.work_min}/{po.break_min}")
        btn_w, btn_h = 90, 24
        btn = QRect(r.x() + (r.width() - btn_w) // 2,
                    r.y() + r.height() - btn_h - 4, btn_w, btn_h)
        if po.active:
            self._draw_label_button(p, btn, "Stop", ACCENT_RED, self.stop_pomodoro)
        else:
            self._draw_label_button(p, btn, "Start", TEXT_PRIMARY,
                                    lambda: self.start_pomodoro())

    # ── Notes card (NEW) ───────────────────────────────────────
    def _paint_notes_card(self, p, rect):
        title_pad = 18
        p.setPen(TEXT_PRIMARY)
        p.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
        p.drawText(QRect(title_pad, 10, rect.width() - 2 * title_pad, 20),
                   Qt.AlignmentFlag.AlignLeft, "Notes")

        # Add button (top-right)
        add_w, add_h = 76, 22
        add_r = QRect(rect.right() - add_w - title_pad, 10, add_w, add_h)
        self._draw_label_button(p, add_r, "+ Add Note", ACCENT_GREEN,
                                self.add_note_dialog)

        if not self.notes:
            p.setPen(TEXT_FAINT)
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRect(title_pad, 50, rect.width() - 2 * title_pad, 40),
                       Qt.AlignmentFlag.AlignCenter,
                       "No notes yet — tap “+ Add Note” to jot one down")
            return

        list_top = 38
        list_bot = rect.bottom() - 22
        per = max(20, (list_bot - list_top) // NOTES_VISIBLE)
        for i, note in enumerate(self.notes[:NOTES_VISIBLE]):
            y = list_top + i * per
            ts = note.get("ts", 0)
            try:
                stamp = datetime.fromtimestamp(ts).strftime("%b %d  %H:%M")
            except Exception:
                stamp = ""
            p.setPen(TEXT_FAINT)
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRect(title_pad, y, 90, per),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       stamp)
            p.setPen(TEXT_PRIMARY)
            p.setFont(QFont("Segoe UI", 10))
            txt = note.get("text", "").replace("\n", " ")
            fm = QFontMetrics(p.font())
            avail = rect.width() - title_pad - 100
            p.drawText(QRect(title_pad + 96, y, avail, per),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(txt, Qt.TextElideMode.ElideRight, avail))

        if len(self.notes) > NOTES_VISIBLE:
            p.setPen(TEXT_FAINT)
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(QRect(title_pad, rect.bottom() - 30,
                             rect.width() - 2 * title_pad, 14),
                       Qt.AlignmentFlag.AlignLeft,
                       f"+ {len(self.notes) - NOTES_VISIBLE} more in {NOTES_PATH}")

    # ── Visual helpers ─────────────────────────────────────────
    def _draw_art(self, p, rect, radius, glyph):
        if self.media.art and not self.media.art.isNull():
            scaled = self.media.art.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            clip = QPainterPath()
            clip.addRoundedRect(rect.x(), rect.y(),
                                rect.width(), rect.height(),
                                radius, radius)
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(rect, scaled)
            p.restore()
        else:
            p.setBrush(QColor(40, 40, 40))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(rect, radius, radius)
            p.setPen(QColor(200, 200, 200))
            p.setFont(QFont("Segoe UI", glyph))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")

    def _draw_eq(self, p, rect):
        bars, bar_w, gap = 3, 3, 2
        total = bars * bar_w + (bars - 1) * gap
        x0 = rect.x() + (rect.width() - total) // 2
        cy = rect.center().y()
        max_h = rect.height() - 8
        playing = self.media.is_playing
        t = time.time() * 5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ACCENT_GREEN if playing else ACCENT_RED)
        for i in range(bars):
            frac = (0.4 + 0.6 * (0.5 + 0.5 * math.sin(t + i * 1.3))
                    if playing else 0.4)
            h = max(2, int(max_h * frac))
            p.drawRoundedRect(x0 + i * (bar_w + gap), cy - h // 2,
                              bar_w, h, 1.5, 1.5)

    def _draw_button(self, p, rect, glyph_drawer, callback, filled=False):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255) if filled else QColor(40, 40, 40))
        p.drawEllipse(rect)
        glyph_drawer(p, rect, filled)
        self._buttons.append((rect, callback))

    def _draw_label_button(self, p, rect, text, color, callback):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(35, 35, 35))
        p.drawRoundedRect(rect, 8, 8)
        p.setPen(color)
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        self._buttons.append((rect, callback))

    def _draw_chip(self, p, rect, text, color, callback):
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.setBrush(QColor(28, 28, 28))
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
    #   Mouse / wheel
    # ────────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
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

    def wheelEvent(self, ev):
        if self.state != State.EXPAND:
            return
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        # Reset accumulator if direction reversed
        if (delta > 0) != (self._scroll_accum >= 0):
            self._scroll_accum = 0
        self._scroll_accum += delta
        # 120 = one mouse-wheel "notch". Trackpad sends ~10-30 per gesture,
        # so we accumulate before flipping the card.
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
        m.addAction("Add Note…", self.add_note_dialog)
        if self.notes:
            m.addAction(f"Clear All Notes  ({len(self.notes)})", self.clear_notes)
        m.addSeparator()
        m.addAction("Start Timer…", self.prompt_timer)
        if self._timer_active():
            m.addAction("Cancel Timer", self.cancel_timer)
        if self.pomodoro.active:
            m.addAction(f"Stop Pomodoro  (cycle {self.pomodoro.cycle})",
                        self.stop_pomodoro)
        else:
            pomo = m.addMenu("Start Pomodoro")
            pomo.addAction("25 / 5",  lambda: self.start_pomodoro(25, 5))
            pomo.addAction("50 / 10", lambda: self.start_pomodoro(50, 10))
            pomo.addAction("90 / 20", lambda: self.start_pomodoro(90, 20))
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
def _glyph_color(filled):
    return QColor(0, 0, 0) if filled else TEXT_PRIMARY


def _draw_play(p, rect, filled=False):
    cx, cy = rect.center().x(), rect.center().y()
    s = rect.width() * 0.22
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled))
    p.drawPolygon(QPolygon([
        QPoint(int(cx - s * 0.8), int(cy - s)),
        QPoint(int(cx - s * 0.8), int(cy + s)),
        QPoint(int(cx + s),       int(cy)),
    ]))


def _draw_pause(p, rect, filled=False):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled))
    bw = rect.width() * 0.14
    gap = rect.width() * 0.14
    h = rect.height() * 0.45
    cx, cy = rect.center().x(), rect.center().y()
    p.drawRoundedRect(int(cx - gap / 2 - bw), int(cy - h / 2),
                      int(bw), int(h), 2, 2)
    p.drawRoundedRect(int(cx + gap / 2), int(cy - h / 2),
                      int(bw), int(h), 2, 2)


def _draw_next(p, rect, filled=False):
    cx, cy = rect.center().x(), rect.center().y()
    s = rect.width() * 0.20
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled))
    p.drawPolygon(QPolygon([
        QPoint(int(cx - s - 2), int(cy - s)),
        QPoint(int(cx - s - 2), int(cy + s)),
        QPoint(int(cx + 2),     int(cy)),
    ]))
    p.drawRoundedRect(int(cx + 4), int(cy - s), 3, int(2 * s), 1, 1)


def _draw_prev(p, rect, filled=False):
    cx, cy = rect.center().x(), rect.center().y()
    s = rect.width() * 0.20
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_glyph_color(filled))
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
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    island = DynamicIsland()

    # Tray
    tray = QSystemTrayIcon(make_tray_icon())
    tray.setToolTip("Dynamic Island")
    menu = QMenu()
    menu.addAction("Show Now", lambda: island.manual_summon(4))
    menu.addAction("Pin / Unpin", island.toggle_pin)
    menu.addSeparator()
    menu.addAction("Add Note…", island.add_note_dialog)
    menu.addAction("Start Timer…", island.prompt_timer)
    pomo = menu.addMenu("Pomodoro")
    pomo.addAction("Start 25 / 5",  lambda: island.start_pomodoro(25, 5))
    pomo.addAction("Start 50 / 10", lambda: island.start_pomodoro(50, 10))
    pomo.addAction("Stop", island.stop_pomodoro)
    sw = menu.addMenu("Stopwatch")
    sw.addAction("Start / Pause", island.stopwatch.toggle)
    sw.addAction("Reset", island.stopwatch.reset)
    menu.addSeparator()
    menu.addAction("Mute Alerts", island.toggle_alerts)
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
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
        island.media_poller.wait(1500)
        island.sys_poller.wait(1500)
        island.weather_poller.wait(1500)

    app.aboutToQuit.connect(_cleanup)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
