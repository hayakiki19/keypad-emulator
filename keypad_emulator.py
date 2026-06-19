"""
KeyPad Emulator — Keyboard & Mouse → Virtual Xbox 360 Controller
================================================================
Requires (Windows only):
1. ViGEmBus driver → https://github.com/ViGEm/ViGEmBus/releases
2. pip install vgamepad pynput

Usage:
    Run as Administrator for mouse button support in games!
    python keypad_emulator.py

Apex Legends Default Layout:
    WASD        = Move (Left Stick)
    Mouse Move  = Look/Aim (Right Stick)
    Left Click  = Fire (RT)
    Right Click = ADS (LT)
    Space       = Jump (A)
    C           = Crouch (B)
    F           = Interact/Reload (X)
    E           = Ping (Y... remapped)
    Q           = Tactical (LB)
    Z           = Ultimate (LB+RB combo via separate key)
    Shift       = Sprint (L-Stick Click)
    G           = Ping (RB)
    Tab         = Inventory (Back)
    4           = Heal Kit (D-Pad Up)
    2           = Weapon Switch (Y)
    V           = Melee (R-Stick Click)
"""

import sys
import threading
import time
import math
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import vgamepad as vg
except ImportError:
    print("ERROR: vgamepad not installed. Run: pip install vgamepad")
    sys.exit(1)

try:
    from pynput import keyboard as kb, mouse as ms
except ImportError:
    print("ERROR: pynput not installed. Run: pip install pynput")
    sys.exit(1)

# ─────────────────────────────────────────────
# Default key mappings — Apex Legends optimized
# ─────────────────────────────────────────────

DEFAULT_MAP = {
    # D-Pad (items / actions in Apex)
    "DPAD_UP":    "4",          # Use Health/Shield Kit
    "DPAD_DOWN":  "3",          # Toggle Fire Mode
    "DPAD_LEFT":  "5",          # Equip Grenade
    "DPAD_RIGHT": "6",          # Extra Character Action

    # Face buttons
    "BTN_A":      "space",      # Jump
    "BTN_B":      "c",          # Crouch
    "BTN_X":      "f",          # Interact / Pickup / Reload
    "BTN_Y":      "2",          # Cycle Weapon / Holster

    # Special
    "BTN_START":  "return",     # Map / Pause
    "BTN_BACK":   "tab",        # Inventory (toggle)
    "BTN_LTHUMB": "shift",      # Sprint / Toggle Zoom (L-Stick Click)
    "BTN_RTHUMB": "v",          # Melee (R-Stick Click)

    # Shoulders / Triggers
    "BTN_LB":     "q",          # Tactical Ability
    "BTN_RB":     "g",          # Ping / Ping Wheel (hold)
    "TRIGGER_LT": "button2",    # ADS — Right Mouse Button
    "TRIGGER_RT": "button1",    # Fire — Left Mouse Button

    # Left stick keys (WASD)
    "LS_UP":      "w",
    "LS_DOWN":    "s",
    "LS_LEFT":    "a",
    "LS_RIGHT":   "d",

    # Extra: Ultimate mapped to Z via LB+RB (hold both Q+Z)
    # Z is mapped to a spare action — wire it to BTN_RB hold in-game
    # or use the LB+RB = Ultimate combo that Apex supports natively
}

BUTTON_FLAGS = {
    "DPAD_UP":    vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "DPAD_DOWN":  vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "DPAD_LEFT":  vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "DPAD_RIGHT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "BTN_A":      vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "BTN_B":      vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "BTN_X":      vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "BTN_Y":      vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    "BTN_START":  vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    "BTN_BACK":   vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    "BTN_LB":     vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "BTN_RB":     vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "BTN_LTHUMB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "BTN_RTHUMB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
}

TRIGGER_BUTTONS = {"TRIGGER_LT", "TRIGGER_RT"}
LS_BUTTONS      = {"LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"}

# ─────────────────────────────────────────────
# Emulator core
# ─────────────────────────────────────────────

class GamepadEmulator:
    def __init__(self, key_map, mouse_sensitivity=0.8, ls_sensitivity=0.3,
                 dead_zone=0.0, stick_smoothing=0.37):
        self.key_map          = key_map
        self.mouse_sensitivity = mouse_sensitivity
        self.ls_sensitivity   = ls_sensitivity
        self.dead_zone        = dead_zone
        self.stick_smoothing  = stick_smoothing

        self.gamepad   = None
        self.active    = False
        self.pressed   = set()

        self._mouse_dx   = 0.0
        self._mouse_dy   = 0.0
        self._mouse_lock = threading.Lock()

        self._kb_listener  = None
        self._ms_listener  = None
        self._update_thread = None

        # smoothed right-stick values
        self._rs_x_smooth = 0.0
        self._rs_y_smooth = 0.0

        self._build_reverse()

    def _build_reverse(self):
        self._reverse = {}
        for action, key_str in self.key_map.items():
            self._reverse.setdefault(key_str.lower(), []).append(action)

    # ── key normalisation ──────────────────────────────────

    def _norm_kb(self, key):
        try:
            return key.char.lower()
        except AttributeError:
            name = str(key).replace("Key.", "").lower()
            return {
                "ctrl_l": "ctrl", "ctrl_r": "ctrl",
                "shift_l": "shift", "shift_r": "shift",
                "alt_l": "alt", "alt_r": "alt",
                "caps_lock": "caps_lock",
            }.get(name, name)

    def _norm_mouse_btn(self, btn):
        """
        pynput Button names:
          Button.left   → button1
          Button.right  → button2
          Button.middle → button3
          Button.x1     → button8  (some mice)
          Button.x2     → button9
        """
        raw = str(btn).lower()
        # Handle named buttons
        if "button.left" in raw:
            return "button1"
        if "button.right" in raw:
            return "button2"
        if "button.middle" in raw:
            return "button3"
        # Already in buttonN format or unknown
        return raw.replace("button.", "button")

    # ── listeners ─────────────────────────────────────────

    def _on_key_press(self, key):
        if not self.active:
            return
        k = self._norm_kb(key)
        self.pressed.add(k)

    def _on_key_release(self, key):
        if not self.active:
            return
        k = self._norm_kb(key)
        self.pressed.discard(k)

    def _on_mouse_move(self, x, y, dx, dy):
        if not self.active:
            return
        with self._mouse_lock:
            self._mouse_dx += dx
            self._mouse_dy += dy

    def _on_mouse_press(self, x, y, btn, pressed):
        if not self.active:
            return
        k = self._norm_mouse_btn(btn)
        if pressed:
            self.pressed.add(k)
        else:
            self.pressed.discard(k)

    def _on_mouse_scroll(self, x, y, dx, dy):
        # Reserved for future use (e.g. weapon scroll)
        pass

    # ── update loop ───────────────────────────────────────

    def _apply_dz(self, v):
        if abs(v) < self.dead_zone:
            return 0.0
        sign = 1 if v > 0 else -1
        return sign * (abs(v) - self.dead_zone) / (1.0 - self.dead_zone)

    def _update_loop(self, fps=60):
        interval = 1.0 / fps
        MAX_MOVE = 20.0  # pixels per frame → full stick

        while self.active:
            t0 = time.perf_counter()

            # ── right stick from mouse ──
            with self._mouse_lock:
                mdx = self._mouse_dx * self.mouse_sensitivity
                mdy = self._mouse_dy * self.mouse_sensitivity
                self._mouse_dx = 0.0
                self._mouse_dy = 0.0

            raw_rs_x = max(-1.0, min(1.0,  mdx / MAX_MOVE))
            raw_rs_y = max(-1.0, min(1.0, -mdy / MAX_MOVE))

            raw_rs_x = self._apply_dz(raw_rs_x)
            raw_rs_y = self._apply_dz(raw_rs_y)

            # Stick smoothing (lerp toward raw value)
            alpha = 1.0 - self.stick_smoothing
            self._rs_x_smooth += alpha * (raw_rs_x - self._rs_x_smooth)
            self._rs_y_smooth += alpha * (raw_rs_y - self._rs_y_smooth)

            rs_x = self._rs_x_smooth
            rs_y = self._rs_y_smooth

            # ── left stick from keys ──
            ls_x = 0.0
            ls_y = 0.0
            for action, key_str in self.key_map.items():
                if key_str.lower() not in self.pressed:
                    continue
                if action == "LS_UP":    ls_y += 1.0
                if action == "LS_DOWN":  ls_y -= 1.0
                if action == "LS_LEFT":  ls_x -= 1.0
                if action == "LS_RIGHT": ls_x += 1.0

            # Normalise diagonal so you don't go faster diagonally
            mag = math.sqrt(ls_x ** 2 + ls_y ** 2)
            if mag > 1.0:
                ls_x /= mag
                ls_y /= mag

            ls_x *= self.ls_sensitivity
            ls_y *= self.ls_sensitivity

            # ── triggers ──
            lt_key = self.key_map.get("TRIGGER_LT", "").lower()
            rt_key = self.key_map.get("TRIGGER_RT", "").lower()
            lt = 255 if lt_key in self.pressed else 0
            rt = 255 if rt_key in self.pressed else 0

            # ── buttons ──
            self.gamepad.reset()

            for action, flag in BUTTON_FLAGS.items():
                key_str = self.key_map.get(action, "")
                if key_str.lower() in self.pressed:
                    self.gamepad.press_button(button=flag)

            self.gamepad.left_joystick_float(ls_x, ls_y)
            self.gamepad.right_joystick_float(rs_x, rs_y)
            self.gamepad.left_trigger(value=lt)
            self.gamepad.right_trigger(value=rt)
            self.gamepad.update()

            elapsed = time.perf_counter() - t0
            sleep = interval - elapsed
            if sleep > 0:
                time.sleep(sleep)

    # ── public API ────────────────────────────────────────

    def start(self):
        if self.active:
            return
        self.gamepad = vg.VX360Gamepad()
        self.active  = True
        self.pressed.clear()

        self._kb_listener = kb.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self._ms_listener = ms.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_press,
            on_scroll=self._on_mouse_scroll
        )
        self._kb_listener.start()
        self._ms_listener.start()

        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

    def stop(self):
        self.active = False
        if self._kb_listener:
            self._kb_listener.stop()
        if self._ms_listener:
            self._ms_listener.stop()
        self._rs_x_smooth = 0.0
        self._rs_y_smooth = 0.0
        self.gamepad = None

# ─────────────────────────────────────────────
# Tkinter GUI
# ─────────────────────────────────────────────

BUTTON_GROUPS = [
    ("D-Pad",               ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"]),
    ("Face Buttons",        ["BTN_A", "BTN_B", "BTN_X", "BTN_Y"]),
    ("Special",             ["BTN_START", "BTN_BACK", "BTN_LTHUMB", "BTN_RTHUMB"]),
    ("Shoulders & Triggers",["BTN_LB", "BTN_RB", "TRIGGER_LT", "TRIGGER_RT"]),
    ("Left Stick Keys",     ["LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"]),
]

FRIENDLY = {
    "DPAD_UP":    "D-Pad Up",
    "DPAD_DOWN":  "D-Pad Down",
    "DPAD_LEFT":  "D-Pad Left",
    "DPAD_RIGHT": "D-Pad Right",
    "BTN_A":      "A (Cross) — Jump",
    "BTN_B":      "B (Circle) — Crouch",
    "BTN_X":      "X (Square) — Interact/Reload",
    "BTN_Y":      "Y (Triangle) — Weapon Switch",
    "BTN_START":  "Start — Map",
    "BTN_BACK":   "Select / Back — Inventory",
    "BTN_LTHUMB": "L-Stick Click — Sprint",
    "BTN_RTHUMB": "R-Stick Click — Melee",
    "BTN_LB":     "LB (L1) — Tactical",
    "BTN_RB":     "RB (R1) — Ping",
    "TRIGGER_LT": "LT (L2) — ADS",
    "TRIGGER_RT": "RT (R2) — Fire",
    "LS_UP":      "Left Stick ↑",
    "LS_DOWN":    "Left Stick ↓",
    "LS_LEFT":    "Left Stick ←",
    "LS_RIGHT":   "Left Stick →",
}

SETUP_TEXT = """INSTALLATION
────────────────────────
1. Install ViGEmBus driver
   → https://github.com/ViGEm/ViGEmBus/releases
   (Download & run the latest .exe installer)

2. Install Python packages
   > pip install vgamepad pynput

3. Run this script AS ADMINISTRATOR
   > Right-click → Run as administrator
   (Required for mouse button capture in games)

HOW TO USE
────────────────────────
• Click "Activate" — Windows will detect a new
  Xbox 360 controller immediately.
• To remap a button: click its key chip on the
  Mapping tab, then press any key or mouse button.
• Mouse movement → Right Stick (camera/aim)
• Left mouse button  → RT (Fire)
• Right mouse button → LT (ADS)
• WASD keys → Left Stick (movement)
• Click "Deactivate" to remove the virtual controller.

APEX LEGENDS LAYOUT
────────────────────────
• Use controller preset: Default
• WASD = move, Mouse = aim
• Left click = fire, Right click = ADS
• Q = Tactical, Z key = spare (LB+RB = Ultimate in Apex)
• G = Ping, Tab = Inventory, Shift = Sprint

NOTES
────────────────────────
• MUST run as Administrator for mouse buttons to work!
• Works with Steam, Epic, Xbox Game Pass, etc.
• Use Steam's "Big Picture" controller config
  for per-game overrides.
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KeyPad Emulator")
        self.resizable(False, False)
        self.configure(bg="#1a1d2e")

        self.key_map   = dict(DEFAULT_MAP)
        self.emulator  = None
        self.active    = False
        self._waiting_for_key = None  # action name being remapped

        self._build_ui()

    # ── UI construction ───────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg="#1a1d2e")
        hdr.pack(fill="x", padx=12, pady=(10, 0))

        tk.Label(hdr, text="⚙  KeyPad Emulator", font=("Segoe UI", 14, "bold"),
                 fg="white", bg="#1a1d2e").pack(side="left")

        self._status_var = tk.StringVar(value="● Inactive")
        self._status_lbl = tk.Label(hdr, textvariable=self._status_var,
                                    font=("Segoe UI", 10), fg="#888", bg="#1a1d2e")
        self._status_lbl.pack(side="left", padx=16)

        self._toggle_btn = tk.Button(
            hdr, text="■  Activate",
            font=("Segoe UI", 10, "bold"),
            bg="#e05c8a", fg="white", relief="flat",
            padx=14, pady=4,
            command=self._toggle
        )
        self._toggle_btn.pack(side="right")

        # Tabs
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._map_frame      = tk.Frame(nb, bg="#1a1d2e")
        self._settings_frame = tk.Frame(nb, bg="#1a1d2e")
        self._setup_frame    = tk.Frame(nb, bg="#1a1d2e")

        nb.add(self._map_frame,      text="Button Mapping")
        nb.add(self._settings_frame, text="Settings")
        nb.add(self._setup_frame,    text="Setup")

        self._build_mapping_tab()
        self._build_settings_tab()
        self._build_setup_tab()

        # Footer
        tk.Label(self, text="Click any key chip to remap  •  Mouse → Right Stick",
                 font=("Segoe UI", 8), fg="#555", bg="#1a1d2e"
                 ).pack(side="bottom", pady=(0, 6))

    def _build_mapping_tab(self):
        canvas  = tk.Canvas(self._map_frame, bg="#1a1d2e", highlightthickness=0, width=680, height=420)
        scrollbar = ttk.Scrollbar(self._map_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg="#1a1d2e")
        canvas.create_window((0, 0), window=inner, anchor="nw")

        self._key_buttons = {}

        # Layout groups in a 3-column grid
        cols = 3
        for idx, (group_name, actions) in enumerate(BUTTON_GROUPS):
            col = idx % cols
            row = idx // cols

            grp = tk.LabelFrame(inner, text=group_name,
                                 font=("Segoe UI", 9, "bold"),
                                 fg="#5ab4f5", bg="#22253a",
                                 padx=8, pady=6, relief="flat",
                                 highlightbackground="#333", highlightthickness=1)
            grp.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

            for action in actions:
                row_f = tk.Frame(grp, bg="#22253a")
                row_f.pack(fill="x", pady=2)

                tk.Label(row_f, text=FRIENDLY.get(action, action),
                         font=("Segoe UI", 9), fg="#bbb", bg="#22253a",
                         width=22, anchor="w").pack(side="left")

                key_val = self.key_map.get(action, "—")
                btn = tk.Button(
                    row_f, text=key_val,
                    font=("Segoe UI", 9, "bold"),
                    fg="#5ab4f5", bg="#2d3150",
                    relief="flat", padx=8, pady=2,
                    cursor="hand2",
                    command=lambda a=action: self._start_remap(a)
                )
                btn.pack(side="right")
                self._key_buttons[action] = btn

        inner.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

    def _build_settings_tab(self):
        f = self._settings_frame
        pad = {"padx": 20, "pady": 10}

        tk.Label(f, text="Mouse Sensitivity", fg="#bbb", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", **pad)
        self._mouse_sens = tk.DoubleVar(value=0.8)
        s1 = ttk.Scale(f, from_=0.1, to=3.0, variable=self._mouse_sens, orient="horizontal", length=300)
        s1.grid(row=0, column=1, **pad)
        tk.Label(f, textvariable=self._mouse_sens, fg="#5ab4f5", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=0, column=2, **pad)

        tk.Label(f, text="Left Stick Speed", fg="#bbb", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", **pad)
        self._ls_speed = tk.DoubleVar(value=0.3)
        s2 = ttk.Scale(f, from_=0.1, to=1.0, variable=self._ls_speed, orient="horizontal", length=300)
        s2.grid(row=1, column=1, **pad)
        tk.Label(f, textvariable=self._ls_speed, fg="#5ab4f5", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=1, column=2, **pad)

        tk.Label(f, text="Dead Zone", fg="#bbb", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", **pad)
        self._dead_zone = tk.DoubleVar(value=0.0)
        s3 = ttk.Scale(f, from_=0.0, to=0.5, variable=self._dead_zone, orient="horizontal", length=300)
        s3.grid(row=2, column=1, **pad)
        tk.Label(f, textvariable=self._dead_zone, fg="#5ab4f5", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=2, column=2, **pad)

        tk.Label(f, text="Stick Smoothing", fg="#bbb", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", **pad)
        self._smoothing = tk.DoubleVar(value=0.37)
        s4 = ttk.Scale(f, from_=0.0, to=0.9, variable=self._smoothing, orient="horizontal", length=300)
        s4.grid(row=3, column=1, **pad)
        tk.Label(f, textvariable=self._smoothing, fg="#5ab4f5", bg="#1a1d2e",
                 font=("Segoe UI", 10)).grid(row=3, column=2, **pad)

        note = ("Right stick is always controlled by mouse movement.\n\n"
                "Lower Stick Smoothing = smoother, more analog-like ramp (recommended for a\n"
                "real \"controller\" feel). Higher = snappier / more instant, closer to raw\n"
                "keyboard & mouse input (can feel jittery in-game).\n\n"
                "NOTE: Left click = Fire (RT) · Right click = ADS (LT)")
        tk.Label(f, text=note, fg="#666", bg="#1a1d2e",
                 font=("Segoe UI", 9), justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=20, pady=10)

    def _build_setup_tab(self):
        txt = tk.Text(self._setup_frame, bg="#1a1d2e", fg="#aaa",
                      font=("Consolas", 9), relief="flat",
                      wrap="word", padx=12, pady=10)
        txt.pack(fill="both", expand=True)
        txt.insert("end", SETUP_TEXT)
        txt.config(state="disabled")

    # ── remapping ─────────────────────────────────────────

    def _start_remap(self, action):
        if self._waiting_for_key:
            return  # already waiting
        self._waiting_for_key = action
        btn = self._key_buttons[action]
        btn.config(text="...", fg="#ffcc00")

        # Listen for next key or mouse button
        self._remap_kb = kb.Listener(on_press=self._capture_key)
        self._remap_ms = ms.Listener(on_click=self._capture_mouse)
        self._remap_kb.start()
        self._remap_ms.start()

    def _capture_key(self, key):
        if not self._waiting_for_key:
            return False

        # Ignore modifier-only presses
        ignore = {"Key.shift", "Key.shift_l", "Key.shift_r",
                  "Key.ctrl_l", "Key.ctrl_r", "Key.alt_l", "Key.alt_r"}
        if str(key) in ignore:
            return

        try:
            k = key.char.lower()
        except AttributeError:
            k = str(key).replace("Key.", "").lower()

        self._apply_remap(k)
        return False  # stop listener

    def _capture_mouse(self, x, y, btn, pressed):
        if not self._waiting_for_key or not pressed:
            return
        k = str(btn).lower()
        if "button.left" in k:
            k = "button1"
        elif "button.right" in k:
            k = "button2"
        elif "button.middle" in k:
            k = "button3"
        else:
            k = k.replace("button.", "button")
        self._apply_remap(k)
        return False

    def _apply_remap(self, new_key):
        action = self._waiting_for_key
        self._waiting_for_key = None

        self.key_map[action] = new_key
        btn = self._key_buttons[action]
        self.after(0, lambda: btn.config(text=new_key, fg="#5ab4f5"))

        try:
            self._remap_kb.stop()
            self._remap_ms.stop()
        except Exception:
            pass

        # If emulator is running, rebuild reverse map
        if self.emulator:
            self.emulator.key_map = self.key_map
            self.emulator._build_reverse()

    # ── activate / deactivate ─────────────────────────────

    def _toggle(self):
        if not self.active:
            self._activate()
        else:
            self._deactivate()

    def _activate(self):
        try:
            self.emulator = GamepadEmulator(
                key_map=self.key_map,
                mouse_sensitivity=round(self._mouse_sens.get(), 2),
                ls_sensitivity=round(self._ls_speed.get(), 2),
                dead_zone=round(self._dead_zone.get(), 2),
                stick_smoothing=round(self._smoothing.get(), 2),
            )
            self.emulator.start()
            self.active = True
            self._status_var.set("● Active — Xbox 360 controller connected")
            self._status_lbl.config(fg="#4caf50")
            self._toggle_btn.config(text="■  Deactivate", bg="#e05c8a")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to activate:\n{e}\n\nTry running as Administrator.")

    def _deactivate(self):
        if self.emulator:
            self.emulator.stop()
            self.emulator = None
        self.active = False
        self._status_var.set("● Inactive")
        self._status_lbl.config(fg="#888")
        self._toggle_btn.config(text="■  Activate", bg="#e05c8a")

    def on_close(self):
        self._deactivate()
        self.destroy()


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
