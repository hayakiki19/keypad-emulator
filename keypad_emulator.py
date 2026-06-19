"""
KeyPad Emulator — Keyboard & Mouse -> Virtual Xbox 360 Controller
================================================================
Requires (Windows only):
1. ViGEmBus driver -> https://github.com/ViGEm/ViGEmBus/releases
2. pip install vgamepad pynput

IMPORTANT: Run as Administrator for mouse buttons to work in games!
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
    "DPAD_UP":    "4",
    "DPAD_DOWN":  "3",
    "DPAD_LEFT":  "5",
    "DPAD_RIGHT": "6",
    "BTN_A":      "space",
    "BTN_B":      "c",
    "BTN_X":      "f",
    "BTN_Y":      "2",
    "BTN_START":  "return",
    "BTN_BACK":   "tab",
    "BTN_LTHUMB": "shift",
    "BTN_RTHUMB": "v",
    "BTN_LB":     "q",
    "BTN_RB":     "g",
    "TRIGGER_LT": "button2",   # right mouse button -> ADS
    "TRIGGER_RT": "button1",   # left mouse button  -> Fire
    "LS_UP":      "w",
    "LS_DOWN":    "s",
    "LS_LEFT":    "a",
    "LS_RIGHT":   "d",
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

# ─────────────────────────────────────────────
# Emulator core
# ─────────────────────────────────────────────

class GamepadEmulator:
    def __init__(self, key_map, mouse_sensitivity=0.8, ls_sensitivity=0.3,
                 dead_zone=0.0, stick_smoothing=0.37):
        self.key_map           = key_map
        self.mouse_sensitivity = mouse_sensitivity
        self.ls_sensitivity    = ls_sensitivity
        self.dead_zone         = dead_zone
        self.stick_smoothing   = stick_smoothing

        self.gamepad    = None
        self.active     = False
        self.pressed    = set()

        # mouse delta tracking — we store last position and compute delta ourselves
        self._mouse_x    = None
        self._mouse_y    = None
        self._mouse_dx   = 0.0
        self._mouse_dy   = 0.0
        self._mouse_lock = threading.Lock()

        self._rs_x_smooth = 0.0
        self._rs_y_smooth = 0.0

        self._kb_listener   = None
        self._ms_listener   = None
        self._update_thread = None

    def _build_reverse(self):
        self._reverse = {}
        for action, key_str in self.key_map.items():
            self._reverse.setdefault(key_str.lower(), []).append(action)

    # ── normalisation ─────────────────────────────────────

    def _norm_kb(self, key):
        try:
            return key.char.lower()
        except AttributeError:
            name = str(key).replace("Key.", "").lower()
            return {
                "ctrl_l": "ctrl", "ctrl_r": "ctrl",
                "shift_l": "shift", "shift_r": "shift",
                "alt_l": "alt", "alt_r": "alt",
            }.get(name, name)

    def _norm_mouse_btn(self, btn):
        raw = str(btn).lower()
        if "left"   in raw: return "button1"
        if "right"  in raw: return "button2"
        if "middle" in raw: return "button3"
        # x1/x2 extra buttons
        return raw.replace("button.", "button")

    # ── listeners ─────────────────────────────────────────

    def _on_key_press(self, key):
        if not self.active: return
        self.pressed.add(self._norm_kb(key))

    def _on_key_release(self, key):
        if not self.active: return
        self.pressed.discard(self._norm_kb(key))

    # pynput on_move signature is (x, y) — absolute position, NO dx/dy
    def _on_mouse_move(self, x, y):
        if not self.active: return
        with self._mouse_lock:
            if self._mouse_x is not None:
                self._mouse_dx += x - self._mouse_x
                self._mouse_dy += y - self._mouse_y
            self._mouse_x = x
            self._mouse_y = y

    def _on_mouse_press(self, x, y, btn, pressed):
        if not self.active: return
        k = self._norm_mouse_btn(btn)
        if pressed:
            self.pressed.add(k)
        else:
            self.pressed.discard(k)

    def _on_mouse_scroll(self, x, y, dx, dy):
        pass  # reserved

    # ── update loop ───────────────────────────────────────

    def _apply_dz(self, v):
        if abs(v) < self.dead_zone: return 0.0
        sign = 1 if v > 0 else -1
        return sign * (abs(v) - self.dead_zone) / (1.0 - self.dead_zone + 1e-9)

    def _update_loop(self, fps=60):
        interval = 1.0 / fps
        MAX_MOVE = 20.0

        while self.active:
            t0 = time.perf_counter()

            # right stick from mouse
            with self._mouse_lock:
                mdx = self._mouse_dx * self.mouse_sensitivity
                mdy = self._mouse_dy * self.mouse_sensitivity
                self._mouse_dx = 0.0
                self._mouse_dy = 0.0

            raw_rs_x = max(-1.0, min(1.0,  mdx / MAX_MOVE))
            raw_rs_y = max(-1.0, min(1.0, -mdy / MAX_MOVE))
            raw_rs_x = self._apply_dz(raw_rs_x)
            raw_rs_y = self._apply_dz(raw_rs_y)

            alpha = 1.0 - self.stick_smoothing
            self._rs_x_smooth += alpha * (raw_rs_x - self._rs_x_smooth)
            self._rs_y_smooth += alpha * (raw_rs_y - self._rs_y_smooth)

            # left stick from keys
            ls_x = ls_y = 0.0
            for action, key_str in self.key_map.items():
                if key_str.lower() not in self.pressed: continue
                if action == "LS_UP":    ls_y += 1.0
                if action == "LS_DOWN":  ls_y -= 1.0
                if action == "LS_LEFT":  ls_x -= 1.0
                if action == "LS_RIGHT": ls_x += 1.0

            mag = math.sqrt(ls_x**2 + ls_y**2)
            if mag > 1.0:
                ls_x /= mag
                ls_y /= mag
            ls_x *= self.ls_sensitivity
            ls_y *= self.ls_sensitivity

            # triggers
            lt = 255 if self.key_map.get("TRIGGER_LT","").lower() in self.pressed else 0
            rt = 255 if self.key_map.get("TRIGGER_RT","").lower() in self.pressed else 0

            # buttons
            self.gamepad.reset()
            for action, flag in BUTTON_FLAGS.items():
                if self.key_map.get(action,"").lower() in self.pressed:
                    self.gamepad.press_button(button=flag)

            self.gamepad.left_joystick_float(ls_x, ls_y)
            self.gamepad.right_joystick_float(self._rs_x_smooth, self._rs_y_smooth)
            self.gamepad.left_trigger(value=lt)
            self.gamepad.right_trigger(value=rt)
            self.gamepad.update()

            elapsed = time.perf_counter() - t0
            rem = interval - elapsed
            if rem > 0: time.sleep(rem)

    # ── public API ────────────────────────────────────────

    def start(self):
        if self.active: return
        self.gamepad = vg.VX360Gamepad()
        self.active  = True
        self.pressed.clear()
        self._mouse_x = None
        self._mouse_y = None

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
        if self._kb_listener: self._kb_listener.stop()
        if self._ms_listener: self._ms_listener.stop()
        self._rs_x_smooth = 0.0
        self._rs_y_smooth = 0.0
        self.gamepad = None


# ─────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────

BUTTON_GROUPS = [
    ("D-Pad",                ["DPAD_UP","DPAD_DOWN","DPAD_LEFT","DPAD_RIGHT"]),
    ("Face Buttons",         ["BTN_A","BTN_B","BTN_X","BTN_Y"]),
    ("Special",              ["BTN_START","BTN_BACK","BTN_LTHUMB","BTN_RTHUMB"]),
    ("Shoulders & Triggers", ["BTN_LB","BTN_RB","TRIGGER_LT","TRIGGER_RT"]),
    ("Left Stick Keys",      ["LS_UP","LS_DOWN","LS_LEFT","LS_RIGHT"]),
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
    "BTN_BACK":   "Select/Back — Inventory",
    "BTN_LTHUMB": "L-Stick Click — Sprint",
    "BTN_RTHUMB": "R-Stick Click — Melee",
    "BTN_LB":     "LB (L1) — Tactical",
    "BTN_RB":     "RB (R1) — Ping",
    "TRIGGER_LT": "LT (L2) — ADS",
    "TRIGGER_RT": "RT (R2) — Fire",
    "LS_UP":      "Left Stick Up",
    "LS_DOWN":    "Left Stick Down",
    "LS_LEFT":    "Left Stick Left",
    "LS_RIGHT":   "Left Stick Right",
}

SETUP_TEXT = """INSTALLATION
────────────────────────────────────
1. Install ViGEmBus driver
   https://github.com/ViGEm/ViGEmBus/releases

2. Install Python packages
   pip install vgamepad pynput

3. RIGHT-CLICK the script -> Run as administrator
   (REQUIRED for mouse buttons to work in games!)

HOW TO USE
────────────────────────────────────
• Click Activate — Xbox 360 controller appears instantly
• Click any key chip to remap it, then press a key or mouse button
• Mouse movement  -> Right Stick (camera / aim)
• Left mouse btn  -> RT (Fire)
• Right mouse btn -> LT (ADS)
• WASD            -> Left Stick (movement)

APEX LEGENDS TIPS
────────────────────────────────────
• Controller preset in Apex: Default
• Ultimate = LB + RB held together (Q + G in this layout)
• Run as Admin or mouse clicks won't register in-game

NOTES
────────────────────────────────────
• Works with Steam, Epic, Xbox Game Pass
• Use Steam Big Picture for per-game overrides
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KeyPad Emulator")
        self.geometry("780x520")
        self.resizable(True, True)
        self.configure(bg="#1a1d2e")

        self.key_map  = dict(DEFAULT_MAP)
        self.emulator = None
        self.active   = False
        self._waiting_for_key = None
        self._remap_kb = None
        self._remap_ms = None

        self._build_ui()

    # ── UI ────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg="#1a1d2e")
        hdr.pack(fill="x", padx=12, pady=(10, 4))

        tk.Label(hdr, text="  KeyPad Emulator",
                 font=("Segoe UI", 14, "bold"),
                 fg="white", bg="#1a1d2e").pack(side="left")

        self._status_var = tk.StringVar(value="Inactive")
        self._status_dot = tk.Label(hdr, text="●", fg="#666", bg="#1a1d2e",
                                    font=("Segoe UI", 11))
        self._status_dot.pack(side="left", padx=(16, 4))
        tk.Label(hdr, textvariable=self._status_var,
                 font=("Segoe UI", 10), fg="#888", bg="#1a1d2e").pack(side="left")

        self._toggle_btn = tk.Button(
            hdr, text="  Activate",
            font=("Segoe UI", 10, "bold"),
            bg="#e05c8a", fg="white", relief="flat",
            padx=14, pady=5, cursor="hand2",
            command=self._toggle
        )
        self._toggle_btn.pack(side="right")

        # Notebook
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background="#1a1d2e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#22253a", foreground="#aaa",
                        padding=[10, 4], font=("Segoe UI", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", "#2d3150")],
                  foreground=[("selected", "white")])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 4))

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
        tk.Label(self,
                 text="Click any key chip to remap  •  Mouse movement -> Right Stick  •  LClick=Fire  RClick=ADS",
                 font=("Segoe UI", 8), fg="#555", bg="#1a1d2e"
                 ).pack(side="bottom", pady=(0, 4))

    def _build_mapping_tab(self):
        # Scrollable canvas
        container = tk.Frame(self._map_frame, bg="#1a1d2e")
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg="#1a1d2e", highlightthickness=0)
        vsb    = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        hsb    = ttk.Scrollbar(container, orient="horizontal", command=canvas.xview)
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg="#1a1d2e")
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def on_resize(e):
            canvas.itemconfig(win_id, width=max(e.width, inner.winfo_reqwidth()))
        canvas.bind("<Configure>", on_resize)

        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))

        # Mouse wheel scroll
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-1*(e.delta/120)), "units"))

        self._key_buttons = {}

        # 3-column grid of groups
        cols = 3
        for idx, (group_name, actions) in enumerate(BUTTON_GROUPS):
            col = idx % cols
            row = idx // cols

            grp = tk.LabelFrame(inner, text=group_name,
                                 font=("Segoe UI", 9, "bold"),
                                 fg="#5ab4f5", bg="#22253a",
                                 padx=8, pady=6,
                                 relief="groove")
            grp.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            inner.columnconfigure(col, weight=1)

            for action in actions:
                row_f = tk.Frame(grp, bg="#22253a")
                row_f.pack(fill="x", pady=2)

                label = FRIENDLY.get(action, action)
                tk.Label(row_f, text=label,
                         font=("Segoe UI", 8), fg="#bbb", bg="#22253a",
                         anchor="w").pack(side="left", fill="x", expand=True)

                key_val = self.key_map.get(action, "—")
                btn = tk.Button(
                    row_f, text=key_val,
                    font=("Segoe UI", 9, "bold"),
                    fg="#5ab4f5", bg="#2d3150",
                    relief="flat", padx=8, pady=2,
                    width=8, cursor="hand2",
                    command=lambda a=action: self._start_remap(a)
                )
                btn.pack(side="right")
                self._key_buttons[action] = btn

    def _build_settings_tab(self):
        f = self._settings_frame

        sliders = [
            ("Mouse Sensitivity",  "_mouse_sens",  0.1, 3.0, 0.8),
            ("Left Stick Speed",   "_ls_speed",    0.1, 1.0, 0.3),
            ("Dead Zone",          "_dead_zone",   0.0, 0.5, 0.0),
            ("Stick Smoothing",    "_smoothing",   0.0, 0.9, 0.37),
        ]

        for i, (label, attr, lo, hi, default) in enumerate(sliders):
            tk.Label(f, text=label, fg="#bbb", bg="#1a1d2e",
                     font=("Segoe UI", 10), anchor="w"
                     ).grid(row=i, column=0, sticky="w", padx=20, pady=10)

            var = tk.DoubleVar(value=default)
            setattr(self, attr, var)

            ttk.Scale(f, from_=lo, to=hi, variable=var,
                      orient="horizontal", length=340
                      ).grid(row=i, column=1, padx=10, pady=10)

            disp = tk.StringVar()
            var.trace_add("write", lambda *a, v=var, d=disp: d.set(f"{v.get():.2f}"))
            disp.set(f"{default:.2f}")
            tk.Label(f, textvariable=disp, fg="#5ab4f5", bg="#1a1d2e",
                     font=("Segoe UI", 10), width=5
                     ).grid(row=i, column=2, padx=6)

        note = ("Right stick is always controlled by mouse movement.\n"
                "Left click = Fire (RT)  •  Right click = ADS (LT)\n\n"
                "Stick Smoothing: lower = smoother analog feel, higher = snappier raw feel.")
        tk.Label(f, text=note, fg="#666", bg="#1a1d2e",
                 font=("Segoe UI", 9), justify="left"
                 ).grid(row=len(sliders), column=0, columnspan=3,
                        sticky="w", padx=20, pady=10)

    def _build_setup_tab(self):
        txt = tk.Text(self._setup_frame, bg="#1a1d2e", fg="#aaa",
                      font=("Consolas", 9), relief="flat",
                      wrap="word", padx=14, pady=10)
        txt.pack(fill="both", expand=True)
        txt.insert("end", SETUP_TEXT)
        txt.config(state="disabled")

    # ── remapping ─────────────────────────────────────────

    def _start_remap(self, action):
        if self._waiting_for_key:
            return
        self._waiting_for_key = action
        self._key_buttons[action].config(text="...", fg="#ffcc00")

        self._remap_kb = kb.Listener(on_press=self._capture_key)
        self._remap_ms = ms.Listener(on_click=self._capture_mouse)
        self._remap_kb.start()
        self._remap_ms.start()

    def _capture_key(self, key):
        if not self._waiting_for_key: return False
        skip = {"Key.shift","Key.shift_l","Key.shift_r",
                "Key.ctrl_l","Key.ctrl_r","Key.alt_l","Key.alt_r"}
        if str(key) in skip: return
        try:
            k = key.char.lower()
        except AttributeError:
            k = str(key).replace("Key.", "").lower()
        self._apply_remap(k)
        return False

    def _capture_mouse(self, x, y, btn, pressed):
        if not self._waiting_for_key or not pressed: return
        raw = str(btn).lower()
        if "left"   in raw: k = "button1"
        elif "right" in raw: k = "button2"
        elif "middle" in raw: k = "button3"
        else: k = raw.replace("button.", "button")
        self._apply_remap(k)
        return False

    def _apply_remap(self, new_key):
        action = self._waiting_for_key
        self._waiting_for_key = None
        self.key_map[action] = new_key
        self.after(0, lambda: self._key_buttons[action].config(
            text=new_key, fg="#5ab4f5"))
        try:
            self._remap_kb.stop()
            self._remap_ms.stop()
        except Exception:
            pass
        if self.emulator:
            self.emulator.key_map = self.key_map
            self.emulator._build_reverse()

    # ── activate / deactivate ─────────────────────────────

    def _toggle(self):
        if not self.active: self._activate()
        else: self._deactivate()

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
            self._status_var.set("Active — Xbox 360 controller connected")
            self._status_dot.config(fg="#4caf50")
            self._toggle_btn.config(text="  Deactivate")
        except Exception as e:
            messagebox.showerror("Error",
                f"Failed to activate:\n{e}\n\nTry running as Administrator.")

    def _deactivate(self):
        if self.emulator:
            self.emulator.stop()
            self.emulator = None
        self.active = False
        self._status_var.set("Inactive")
        self._status_dot.config(fg="#666")
        self._toggle_btn.config(text="  Activate")

    def on_close(self):
        self._deactivate()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
