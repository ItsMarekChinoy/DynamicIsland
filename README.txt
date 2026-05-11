DYNAMIC ISLAND FOR LAPTOP  ·  v4
=================================

No API keys needed. Works out of the box.


WHAT'S NEW IN v4
----------------
• THEMES                 — 7 colour schemes. Right-click → Theme.
                            Persisted to ~/.dynamic_island_settings.json
• VOLUME CONTROL         — new Audio card. Click on the bar to set it,
                            scroll on the bar to nudge ±5%, or use the
                            +/− and Mute buttons. The menu also has a
                            Volume submenu.
• MUTE / UNMUTE ANIMATIONS — when system volume or mute changes (from
                            anywhere — your media keys, the island,
                            or any other app) a pill drops down with
                            a speaker icon and a live level bar. Mute
                            draws a strike-through that sweeps across
                            the speaker.
• FILE HOLDER            — drag any file from Explorer / Finder onto
                            the island. It expands and stores the file.
                            Switch to the Files card and drag a row OUT
                            into a browser upload field, an email
                            compose window, Discord, anywhere.


INSTALL  (Windows)
------------------
1. Install Python 3.10 or newer from https://www.python.org/downloads/
   IMPORTANT: tick "Add Python to PATH" on the first screen of the
   installer. This is the #1 thing people miss and it breaks everything.

2. Unzip this folder somewhere (Desktop is fine).

3. Double-click  run.bat
   - The cmd window will stay open while it installs dependencies.
   - First launch takes ~30 seconds. Subsequent launches are instant.
   - The cmd window must stay open while you use the island.
     You can minimise it. Don't close it.

4. Hover the top-middle edge of your screen (near the camera).
   The island appears.


INSTALL  (macOS / Linux)
------------------------
1. Open Terminal in this folder.
2. Run:    chmod +x run.sh && ./run.sh
3. On Linux, also install:
       sudo apt install playerctl pulseaudio-utils
   (playerctl is for media display, pactl is for volume control)


HOW TO USE
----------
• Hover top-middle edge of screen     →  expands the island
• Move mouse away                     →  collapses to a small pill
• Scroll wheel on island              →  switch cards
                                          (Media · Clock · System · Audio
                                           · Timers · Notes · Files)
• Scroll on the Audio card            →  changes volume (±5% per click)
• Right-click the island              →  menu (theme, volume, files,
                                          timer, pomodoro, notes, pin,
                                          mute, quit)
• Ctrl + Shift + Space                →  summon manually (if 'keyboard'
                                          lib installed)
• System tray icon                    →  same menu, plus "Show Now"


HOLDING & DROPPING FILES
------------------------
1. Drag any file from File Explorer / Finder / Nautilus onto the island
   (or onto the very top edge of the screen — that's the hidden
   landing zone).
2. The island expands, switches to the Files card, and shows your file.
3. To drop the file somewhere else: open the destination first (browser
   upload, email compose, Discord, etc), hover the top of the screen so
   the island expands, then drag the file row OUT of the island and
   into the destination.
4. Up to 12 files can be held at once. Use the X button to remove one,
   or "Clear All" to drop them all.

Note: the island holds a *reference* to each file (its path on disk).
If you delete or move the file, the row will say "(missing)". Holding a
file does not copy it.


THEMES
------
Right-click the island → Theme submenu.
The 7 themes:
   • Classic Black     — original look
   • Midnight Blue     — deep navy
   • Forest            — pine + moss
   • Sunset            — warm reds and oranges
   • Cyberpunk         — neon purple, pink, cyan
   • Ocean             — teal and seafoam
   • Mono Light        — light theme for use during the day

Your choice is saved between launches.


VOLUME CONTROL
--------------
You need a backend that lets us read & set the master volume:
   Windows  : pip install pycaw  (auto-installed by run.bat)
   macOS    : nothing extra — uses osascript
   Linux    : pactl (Pulse/PipeWire) or amixer (ALSA)

Without a backend, +/- buttons still work via OS keystrokes (Windows),
but the level bar can't be displayed.

Click anywhere on the bar to jump to that level. Scroll on the bar to
nudge ±5%. The Mute button is in the centre.


WHEN THE ISLAND APPEARS ON ITS OWN
-----------------------------------
• Music starts playing
• A timer or pomodoro is running
• You copy text (purple "Copied …" pill, ~2.5s)
• System volume changes or mute toggles (animated pill, ~1.6s)
• You drag a file onto the screen-top
• Battery hits low, or charger plugged/unplugged


TROUBLESHOOTING
---------------
"Window opens then closes immediately":
   You're using an old run.bat. Use the new one in this zip.
   The new one stays open and shows you the error.

"Python is not installed or not on PATH":
   You skipped the "Add Python to PATH" tickbox.
   Re-run the Python installer → Modify → tick "Add Python to PATH".

"Could not install PyQt6 / Norton blocked it":
   - Add this folder to Norton's exclusion list.
   - Or right-click run.bat → Run as administrator.

"keyboard failed to install":
   This is fine. Norton often blocks the keyboard library because it
   can read keystrokes. The island still works without it — you just
   can't use Ctrl+Shift+Space to summon it. Hover instead.

"pycaw failed to install on Windows":
   You'll still get a working Mute button (uses the OS mute key) and
   working +/- buttons (uses the OS volume keys), but the level bar
   on the Audio card and the live volume animations won't work.
   Try:    pip install pycaw comtypes

"Volume bar shows 'Volume read unavailable' on Linux":
   Install pactl:    sudo apt install pulseaudio-utils
   Or amixer:        sudo apt install alsa-utils

"Drag-and-drop into the island doesn't work":
   - Try expanding the island first (hover the top of the screen),
     then drop the file onto the expanded panel.
   - Some apps (especially admin-elevated ones) block DnD to non-elevated
     receivers. Run Explorer and the island at the same elevation level.

"I dragged a file out but the destination didn't accept it":
   Check the destination accepts file URLs (most browser upload fields,
   email apps and chat apps do). Some apps only accept text.

Island appears in the wrong spot:
   Open dynamic_island.py and edit the constants near the top:
   PILL_W, PILL_H, EXPAND_W, EXPAND_H, TOP_MARGIN, HOTZONE_W.

Linux media doesn't show:
   sudo apt install playerctl


AUTO-START ON LOGIN (Windows)
------------------------------
Press Win+R, type:   shell:startup
Drag a SHORTCUT to run.bat into the folder that opens.
(Right-click run.bat → Send to → Desktop, then move that shortcut.)


FILES THIS APP WRITES TO YOUR HOME FOLDER
------------------------------------------
~/.dynamic_island_settings.json   theme & alerts-mute preference
~/.dynamic_island_notes.json      your quick notes

Files dropped onto the island are NOT copied — only their paths are
held in memory and forgotten on quit.


API KEYS
--------
None required. Everything works out of the box.

• Weather:  uses wttr.in (free, no key, IP-located)
• Media:    uses Windows' built-in SMTC API
• System:   psutil reads CPU/RAM/battery/network locally
• Volume:   pycaw (Win) / osascript (mac) / pactl|amixer (Linux)
