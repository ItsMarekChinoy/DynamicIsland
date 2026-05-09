DYNAMIC ISLAND FOR LAPTOP
==========================

No API keys needed. Works out of the box.


INSTALL  (Windows)
------------------
1. Install Python 3.10 or newer from https://www.python.org/downloads/
   IMPORTANT: tick "Add Python to PATH" on the first screen of the installer.
   This is the #1 thing people miss and it breaks everything.

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
3. On Linux, also install playerctl for media support:
       sudo apt install playerctl


HOW TO USE
----------
• Hover top-middle edge of screen     →  expands the island
• Move mouse away                     →  collapses to a small pill
• Scroll wheel on island              →  switch cards (Media / Clock / System / Timers / Notes)
• Right-click the island              →  menu (timer, pomodoro, notes, pin, mute, quit)
• Ctrl + Shift + Space                →  summon manually (if 'keyboard' lib installed)
• System tray icon                    →  same menu, plus "Show Now"


WHEN THE ISLAND APPEARS ON ITS OWN
-----------------------------------
• Music starts playing
• A timer or pomodoro is running
• You copy text (purple "Copied …" pill, ~2.5s)
• Battery hits low, or charger plugged/unplugged


TROUBLESHOOTING
---------------
"Window opens then closes immediately":
   You're using an old run.bat. Use the new one in this zip.
   The new one stays open and shows you the error.

"Python is not installed or not on PATH":
   You skipped the "Add Python to PATH" tickbox.
   Re-run the Python installer → choose Modify → tick "Add Python to PATH".

"Could not install PyQt6 / Norton blocked it":
   - Add this folder to Norton's exclusion list.
   - Or right-click run.bat → Run as administrator.

"keyboard failed to install":
   This is fine. Norton often blocks the keyboard library because it can
   read keystrokes (which is also why it's the only thing that lets us
   register a global hotkey). It's a popular open-source library, not
   malware, but Norton flags it sometimes. The island still works
   without it - you just can't use Ctrl+Shift+Space to summon it.
   Hover the top of your screen instead.

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


API KEYS
--------
None required. Everything works out of the box.

• Weather:  uses wttr.in (free, no key, IP-located)
• Media:    uses Windows' built-in SMTC API
• System:   psutil reads CPU/RAM/battery/network locally
