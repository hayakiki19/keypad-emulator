"""
KeyPad Emulator — Keyboard & Mouse → Virtual Xbox 360 Controller
================================================================

Requires (Windows only):
  1. ViGEmBus driver → https://github.com/ViGEm/ViGEmBus/releases
  2. pip install vgamepad pynput

Usage:
  python keypad_emulator.py

The script opens a small Tkinter config window.
Click "Activate" to create a virtual Xbox 360 controller that
Windows and any game will detect as a real gamepad.
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
# Default key mappings (key = pynput key name or char)
# ─────────────────────────────────────────────
DEFAULT_MAP = {
    # D-Pad
    "DPAD_UP": "w",
    "DPAD_DOWN": "s",
    "DPAD_LEFT": "a",
    "DPAD_RIGHT": "d",
    # Face buttons
    "BTN_A": "space",
    "BTN_B": "f",
    "BTN_X": "r",
    "BTN_Y": "e",
    "BTN_START": "return",
    "BTN_BACK": "backspace",
    "BTN_LTHUMB": "q",
    "BTN_RTHUMB": "button8",  # mouse button 3 (middle)
    # Shoulders / Triggers
    "BTN_LB": "shift",
    "BTN_RB": "ctrl",
    "TRIGGER_LT": "z",
    "TRIGGER_RT": "button2",  # right mouse button
    # Left stick (WASD variant for secondary stick)
    "LS_UP": "i",
    "LS_DOWN": "k",
    "LS_LEFT": "j",
    "LS_RIGHT": "l",
}

BUTTON_FLAGS = {
    "DPAD_UP": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "DPAD_DOWN": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "DPAD_LEFT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "DPAD_RIGHT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "BTN_A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "BTN_B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "BTN_X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "BTN_Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    "BTN_START": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    "BTN_BACK": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    "BTN_LB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "BTN_RB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "BTN_LTHUMB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "BTN_RTHUMB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
}

TRIGGER_BUTTONS = {"TRIGGER_LT", "TRIGGER_RT"}
LS_BUTTONS = {"LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"}


# ─────────────────────────────────────────────
# Emulator core
# ─────────────────────────────────────────────
class GamepadEmulator:
    def __init__(self, key_map, mouse_sensitivity=0.5, ls_sensitivity=1.0, dead_zone=0.1):
        self.key_map = key_map  # action → key/button string
        self.mouse_sensitivity = mouse_sensitivity
        self.ls_sensitivity = ls_sensitivity
        self.dead_zone = dead_zone

        self.gamepad = None
        self.active = False
        self.pressed = set()

        self._mouse_dx = 0.0
        self._mouse_dy = 0.0
        self._mouse_lock = threading.Lock()
        self._last_mouse_pos = None  # used to derive dx/dy from raw x, y

        self._kb_listener = None
        self._ms_listener = None
        self._update_thread = None

        # build reverse map: key_string → [action, ...]
        self._reverse = {}
        for action, key_str in self.key_map.items():
            self._reverse.setdefault(key_str.lower(), []).append(action)

    # ── key normalisation ──────────────────────────────────
    def _norm_kb(self, key):
        try:
            return key.char.lower()
        except AttributeError:
            name = str(key).replace("Key.", "").lower()
            # map common names
            return {"ctrl_l": "ctrl", "ctrl_r": "ctrl",
                     "shift_l": "shift", "shift_r": "shift",
                     "alt_l": "alt", "alt_r": "alt"}.get(name, name)

    def _norm_mouse_btn(self, btn):
        name = str(btn).replace("Button.", "button").lower()
        return name  # button2 = right, button8 = middle, etc.

    # ── listeners ─────────────────────────────────────────
    def _on_key_press(self, key):
        if not self.active: return
        k = self._norm_kb(key)
        if k not in self.pressed:
            self.pressed.add(k)

    def _on_key_release(self, key):
        if not self.active: return
        k = self._norm_kb(key)
        self.pressed.discard(k)

    def _on_mouse_move(self, x, y):
        # pynput's mouse listener only ever calls on_move with (x, y) —
        # it does NOT supply dx/dy directly, so we derive the delta
        # ourselves from the previous known position.
        if not self.active:
            return
        if self._last_mouse_pos is None:
            self._last_mouse_pos = (x, y)
            return
        dx = x - self._last_mouse_pos[0]
        dy = y - self._last_mouse_pos[1]
        self._last_mouse_pos = (x, y)
        with self._mouse_lock:
            self._mouse_dx += dx
            self._mouse_dy += dy

    def _on_mouse_press(self, x, y, btn, pressed):
        if not self.active: return
        k = self._norm_mouse_btn(btn)
        if pressed:
            self.pressed.add(k)
        else:
            self.pressed.discard(k)

    # ── update loop ───────────────────────────────────────
    def _update_loop(self, fps=60):
        interval = 1.0 / fps
        MAX_AXIS = 32767
        axis_accel = {}  # for smooth stick movement

        while self.active:
            t0 = time.perf_counter()

            # --- right stick from mouse ---
            with self._mouse_lock:
                mdx = self._mouse_dx * self.mouse_sensitivity
                mdy = self._mouse_dy * self.mouse_sensitivity
                self._mouse_dx = 0.0
                self._mouse_dy = 0.0

            rs_x = max(-1.0, min(1.0, mdx / 20.0))
            rs_y = max(-1.0, min(1.0, -mdy / 20.0))

            # dead zone
            def apply_dz(v):
                if abs(v) < self.dead_zone: return 0.0
                sign = 1 if v > 0 else -1
                return sign * (abs(v) - self.dead_zone) / (1.0 - self.dead_zone)

            rs_x = apply_dz(rs_x)
            rs_y = apply_dz(rs_y)

            # --- left stick from keys ---
            ls_x = 0.0
            ls_y = 0.0
            for action, key_str in self.key_map.items():
                if key_str.lower() not in self.pressed:
                    continue
                if action == "LS_UP": ls_y += 1.0
                if action == "LS_DOWN": ls_y -= 1.0
                if action == "LS_LEFT": ls_x -= 1.0
                if action == "LS_RIGHT": ls_x += 1.0

            # normalise diagonal
            mag = math.sqrt(ls_x**2 + ls_y**2)
            if mag > 1.0:
                ls_x /= mag
                ls_y /= mag
            ls_x *= self.ls_sensitivity
            ls_y *= self.ls_sensitivity

            # --- triggers ---
            lt = 255 if self.key_map.get("TRIGGER_LT", "").lower() in self.pressed else 0
            rt = 255 if self.key_map.get("TRIGGER_RT", "").lower() in self.pressed else 0

            # --- buttons ---
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
        if self.active: return
        self.gamepad = vg.VX360Gamepad()
        self.active = True
        self.pressed.clear()
        self._last_mouse_pos = None

        self._kb_listener = kb.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
        self._ms_listener = ms.Listener(on_move=self._on_mouse_move, on_click=self._on_mouse_press)
        self._kb_listener.start()
        self._ms_listener.start()

        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

    def stop(self):
        self.active = False
        if self._kb_listener: self._kb_listener.stop()
        if self._ms_listener: self._ms_listener.stop()
        self.gamepad = None


# ─────────────────────────────────────────────
# Tkinter GUI
# ─────────────────────────────────────────────
BUTTON_GROUPS = [
    ("D-Pad", ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"]),
    ("Face Buttons", ["BTN_A", "BTN_B", "BTN_X", "BTN_Y"]),
    ("Special", ["BTN_START", "BTN_BACK", "BTN_LTHUMB", "BTN_RTHUMB"]),
    ("Shoulders & Triggers", ["BTN_LB", "BTN_RB", "TRIGGER_LT", "TRIGGER_RT"]),
    ("Left Stick Keys", ["LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"]),
]

FRIENDLY = {
    "DPAD_UP": "D-Pad Up", "DPAD_DOWN": "D-Pad Down", "DPAD_LEFT": "D-Pad Left", "DPAD_RIGHT": "D-Pad Right",
    "BTN_A": "A (Cross)", "BTN_B": "B (Circle)", "BTN_X": "X (Square)", "BTN_Y": "Y (Triangle)",
    "BTN_START": "Start", "BTN_BACK": "Select / Back",
    "BTN_LTHUMB": "L-Stick Click", "BTN_RTHUMB": "R-Stick Click",
    "BTN_LB": "LB (L1)", "BTN_RB": "RB (R1)", "TRIGGER_LT": "LT (L2)", "TRIGGER_RT": "RT (R2)",
    "LS_UP": "Left Stick ↑", "LS_DOWN": "Left Stick ↓", "LS_LEFT": "Left Stick ←", "LS_RIGHT": "Left Stick →",
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KeyPad Emulator")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")

        self.key_map = dict(DEFAULT_MAP)
        self.emulator = None
        self.active = False
        self.listening_for = None
        self.entry_vars = {}
        self.entry_widgets = {}

        self._build_ui()

    def _build_ui(self):
        BG = "#1e1e2e"
        CARD = "#2a2a3e"
        FG = "#cdd6f4"
        ACC = "#89b4fa"
        MUTED = "#6c7086"

        # Header
        hdr = tk.Frame(self, bg="#11111b", pady=10, padx=20)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⚙ KeyPad Emulator", font=("Segoe UI", 14, "bold"),
                 bg="#11111b", fg=FG).pack(side=tk.LEFT)
        self.status_lbl = tk.Label(hdr, text="● Inactive", font=("Segoe UI", 11),
                                    bg="#11111b", fg=MUTED)
        self.status_lbl.pack(side=tk.LEFT, padx=20)

        # Toggle button
        self.toggle_btn = tk.Button(hdr, text="▶ Activate", font=("Segoe UI", 10, "bold"),
                                     bg="#313244", fg=FG, relief=tk.FLAT, padx=12, pady=4,
                                     cursor="hand2", command=self.toggle_emulator)
        self.toggle_btn.pack(side=tk.RIGHT)

        # Notebook for tabs
        nb_frame = tk.Frame(self, bg=BG, padx=16, pady=12)
        nb_frame.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD, foreground=FG,
                         padding=[10, 5], font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", "#45475a")], foreground=[("selected", FG)])

        nb = ttk.Notebook(nb_frame)
        nb.pack(fill=tk.BOTH, expand=True)

        # --- Mapping tab ---
        map_frame = tk.Frame(nb, bg=BG)
        nb.add(map_frame, text="Button Mapping")

        canvas = tk.Canvas(map_frame, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(map_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        col = 0
        for group_name, actions in BUTTON_GROUPS:
            grp = tk.LabelFrame(inner, text=f" {group_name} ", font=("Segoe UI", 10, "bold"),
                                 bg=CARD, fg=ACC, bd=0, padx=10, pady=8, labelanchor="nw")
            grp.grid(row=0, column=col, padx=8, pady=8, sticky="n")

            for action in actions:
                row_f = tk.Frame(grp, bg=CARD)
                row_f.pack(fill=tk.X, pady=3)
                tk.Label(row_f, text=FRIENDLY[action], font=("Segoe UI", 10),
                         bg=CARD, fg=FG, width=16, anchor="w").pack(side=tk.LEFT)

                var = tk.StringVar(value=self.key_map[action])
                self.entry_vars[action] = var

                btn = tk.Button(row_f, textvariable=var, font=("Segoe UI Mono", 10, "bold"),
                                 bg="#313244", fg=ACC, relief=tk.FLAT, width=10,
                                 cursor="hand2", command=lambda a=action: self.start_listen(a))
                btn.pack(side=tk.LEFT, padx=4)
                self.entry_widgets[action] = btn
            col += 1

        # --- Settings tab ---
        settings_frame = tk.Frame(nb, bg=BG, padx=20, pady=16)
        nb.add(settings_frame, text="Settings")

        def slider_row(parent, label, from_, to, initial, fmt=lambda v: str(int(v))):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill=tk.X, pady=8)
            tk.Label(f, text=label, font=("Segoe UI", 10), bg=BG, fg=FG, width=20, anchor="w").pack(side=tk.LEFT)
            val_lbl = tk.Label(f, text=fmt(initial), font=("Segoe UI", 10, "bold"), bg=BG, fg=ACC, width=5)
            val_lbl.pack(side=tk.RIGHT)
            scale = ttk.Scale(f, from_=from_, to=to, orient=tk.HORIZONTAL,
                               command=lambda v, l=val_lbl, f=fmt: l.config(text=f(float(v))))
            scale.set(initial)
            scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
            return scale

        self.ms_scale = slider_row(settings_frame, "Mouse Sensitivity", 0.1, 2.0, 0.5, lambda v: f"{v:.1f}")
        self.ls_scale = slider_row(settings_frame, "Left Stick Speed", 0.1, 2.0, 1.0, lambda v: f"{v:.1f}")
        self.dz_scale = slider_row(settings_frame, "Dead Zone", 0.0, 0.5, 0.1, lambda v: f"{int(v*100)}%")

        tk.Label(settings_frame, text="Right stick is always controlled by mouse movement.",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(16, 0))

        # Instructions
        inst_frame = tk.Frame(nb, bg=BG, padx=20, pady=16)
        nb.add(inst_frame, text="Setup")

        instructions = """INSTALLATION
━━━━━━━━━━━━━━━━━━━━━━
1. Install ViGEmBus driver
   → https://github.com/ViGEm/ViGEmBus/releases
   (Download & run the latest .exe installer)

2. Install Python packages
   > pip install vgamepad pynput

3. Run this script
   > python keypad_emulator.py

HOW TO USE
━━━━━━━━━━━━━━━━━━━━━━
• Click "Activate" — Windows will detect a new
  Xbox 360 controller immediately.
• To remap a button: click its key chip on the
  Mapping tab, then press any key.
• Mouse movement → Right Stick (camera/aim)
• IJKL keys → Left Stick (by default)
• Right mouse button → RT trigger
• Click "Deactivate" to remove the virtual controller.

NOTES
━━━━━━━━━━━━━━━━━━━━━━
• Works with Steam, Epic, Xbox Game Pass, etc.
• Use Steam's "Big Picture" controller config
  for per-game overrides.
• Run as Administrator if the driver isn't detected.
"""
        tk.Label(inst_frame, text=instructions, font=("Consolas", 10),
                 bg=BG, fg=FG, justify=tk.LEFT).pack(anchor="w")

        # Footer
        foot = tk.Frame(self, bg="#11111b", pady=6, padx=16)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(foot, text="Click any key chip to remap • Mouse → Right Stick",
                 font=("Segoe UI", 9), bg="#11111b", fg=MUTED).pack(side=tk.LEFT)

        self.geometry("860x520")

    # ── listen for key press to remap ──────────────────────
    def start_listen(self, action):
        if self.listening_for:
            w = self.entry_widgets.get(self.listening_for)
            if w: w.config(bg="#313244", fg="#89b4fa")

        self.listening_for = action
        w = self.entry_widgets[action]
        self.entry_vars[action].set("Press…")
        w.config(bg="#45475a", fg="#f38ba8")

        self.bind("<KeyPress>", self._capture_key)
        self.bind("<Button-1>", self._capture_mouse_btn)
        self.bind("<Button-2>", self._capture_mouse_btn)
        self.bind("<Button-3>", self._capture_mouse_btn)

    def _capture_key(self, event):
        if not self.listening_for: return
        key_name = event.keysym.lower()
        if key_name in ("escape",):
            self._cancel_listen()
            return
        self._apply_capture(key_name)

    def _capture_mouse_btn(self, event):
        if not self.listening_for: return
        # ignore clicks on the button chip itself
        w = self.entry_widgets.get(self.listening_for)
        if event.widget is w: return
        btn_map = {1: "button", 2: "button2", 3: "button3"}
        self._apply_capture(btn_map.get(event.num, f"button{event.num}"))

    def _apply_capture(self, key_name):
        action = self.listening_for
        self.key_map[action] = key_name
        self.entry_vars[action].set(key_name)
        w = self.entry_widgets[action]
        w.config(bg="#313244", fg="#89b4fa")
        self.listening_for = None
        self.unbind("<KeyPress>")
        self.unbind("<Button-1>")
        self.unbind("<Button-2>")
        self.unbind("<Button-3>")

        # hot-reload emulator mapping if active
        if self.emulator:
            self.emulator.key_map = dict(self.key_map)

    def _cancel_listen(self):
        action = self.listening_for
        if action:
            self.entry_vars[action].set(self.key_map[action])
            self.entry_widgets[action].config(bg="#313244", fg="#89b4fa")
        self.listening_for = None
        self.unbind("<KeyPress>")

    # ── toggle emulator ────────────────────────────────────
    def toggle_emulator(self):
        if not self.active:
            try:
                emu = GamepadEmulator(
                    key_map=dict(self.key_map),
                    mouse_sensitivity=self.ms_scale.get(),
                    ls_sensitivity=self.ls_scale.get(),
                    dead_zone=self.dz_scale.get(),
                )
                emu.start()
                self.emulator = emu
                self.active = True
                self.status_lbl.config(text="● Active — Xbox 360 controller connected", fg="#a6e3a1")
                self.toggle_btn.config(text="■ Deactivate", bg="#f38ba8", fg="#1e1e2e")
            except Exception as ex:
                messagebox.showerror("Error", f"Could not start emulator:\n{ex}\n\n"
                                               "Make sure ViGEmBus driver is installed.")
        else:
            if self.emulator:
                self.emulator.stop()
                self.emulator = None
            self.active = False
            self.status_lbl.config(text="● Inactive", fg="#6c7086")
            self.toggle_btn.config(text="▶ Activate", bg="#313244", fg="#cdd6f4")

    def on_close(self):
        if self.emulator:
            self.emulator.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
