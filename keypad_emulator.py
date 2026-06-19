"""
KeyPad Emulator — Keyboard & Mouse → Virtual Xbox 360 Controller
================================================================
Requires (Windows only):
  1. ViGEmBus driver  → https://github.com/ViGEm/ViGEmBus/releases
  2. pip install vgamepad pynput

Usage:
  python keypad_emulator.py
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
# Default key mappings
# Mouse button names: buttonleft, buttonright, buttonmiddle, buttonx1, buttonx2
# ─────────────────────────────────────────────

DEFAULT_MAP = {
    # D-Pad
    "DPAD_UP":    "4",
    "DPAD_DOWN":  "3",
    "DPAD_LEFT":  "5",
    "DPAD_RIGHT": "6",
    # Face buttons
    "BTN_A": "space",
    "BTN_B": "c",
    "BTN_X": "f",
    "BTN_Y": "2",
    # Special
    "BTN_START":  "return",
    "BTN_BACK":   "tab",
    "BTN_LTHUMB": "shift",
    "BTN_RTHUMB": "v",
    # Shoulders / Triggers
    "BTN_LB":     "q",
    "BTN_RB":     "g",
    "TRIGGER_LT": "buttonright",   # Right mouse button → LT (ADS)
    "TRIGGER_RT": "buttonleft",    # Left mouse button  → RT (Fire)
    # Left stick (movement)
    "LS_UP":    "w",
    "LS_DOWN":  "s",
    "LS_LEFT":  "a",
    "LS_RIGHT": "d",
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
    def __init__(self, key_map, mouse_sensitivity=0.5, ls_sensitivity=1.0, dead_zone=0.1):
        self.key_map           = key_map
        self.mouse_sensitivity = mouse_sensitivity
        self.ls_sensitivity    = ls_sensitivity
        self.dead_zone         = dead_zone
        self.gamepad           = None
        self.active            = False
        self.pressed           = set()

        # Mouse delta — tracked by diffing absolute positions
        self._mouse_x      = None
        self._mouse_y      = None
        self._mouse_dx     = 0.0
        self._mouse_dy     = 0.0
        self._mouse_lock   = threading.Lock()

        self._kb_listener   = None
        self._ms_listener   = None
        self._update_thread = None
        self._rebuild_reverse()

    def _rebuild_reverse(self):
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
                "alt_l": "alt",  "alt_r": "alt",
            }.get(name, name)

    def _norm_mouse_btn(self, btn):
        # pynput gives e.g. Button.left → "buttonleft"
        name = str(btn).lower().replace("button.", "button")
        return name

    # ── pynput listeners ──────────────────────────────────

    def _on_key_press(self, key):
        if not self.active:
            return
        self.pressed.add(self._norm_kb(key))

    def _on_key_release(self, key):
        if not self.active:
            return
        self.pressed.discard(self._norm_kb(key))

    def _on_mouse_move(self, x, y):
        """pynput on_move passes absolute (x, y) — we compute delta ourselves."""
        if not self.active:
            return
        with self._mouse_lock:
            if self._mouse_x is not None:
                self._mouse_dx += x - self._mouse_x
                self._mouse_dy += y - self._mouse_y
            self._mouse_x = x
            self._mouse_y = y

    def _on_mouse_click(self, x, y, btn, pressed):
        if not self.active:
            return
        k = self._norm_mouse_btn(btn)
        if pressed:
            self.pressed.add(k)
        else:
            self.pressed.discard(k)

    # ── update loop ───────────────────────────────────────

    def _update_loop(self, fps=60):
        interval = 1.0 / fps

        while self.active:
            t0 = time.perf_counter()

            # Right stick ← mouse delta
            with self._mouse_lock:
                mdx = self._mouse_dx * self.mouse_sensitivity
                mdy = self._mouse_dy * self.mouse_sensitivity
                self._mouse_dx = 0.0
                self._mouse_dy = 0.0

            rs_x = max(-1.0, min(1.0, mdx / 20.0))
            rs_y = max(-1.0, min(1.0, -mdy / 20.0))

            def apply_dz(v):
                if abs(v) < self.dead_zone:
                    return 0.0
                sign = 1 if v > 0 else -1
                return sign * (abs(v) - self.dead_zone) / (1.0 - self.dead_zone)

            rs_x = apply_dz(rs_x)
            rs_y = apply_dz(rs_y)

            # Left stick ← WASD keys
            ls_x = ls_y = 0.0
            for action, key_str in self.key_map.items():
                if key_str.lower() not in self.pressed:
                    continue
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

            # Triggers
            lt = 255 if self.key_map.get("TRIGGER_LT", "").lower() in self.pressed else 0
            rt = 255 if self.key_map.get("TRIGGER_RT", "").lower() in self.pressed else 0

            # Buttons
            self.gamepad.reset()
            for action, flag in BUTTON_FLAGS.items():
                if self.key_map.get(action, "").lower() in self.pressed:
                    self.gamepad.press_button(button=flag)

            self.gamepad.left_joystick_float(ls_x, ls_y)
            self.gamepad.right_joystick_float(rs_x, rs_y)
            self.gamepad.left_trigger(value=lt)
            self.gamepad.right_trigger(value=rt)
            self.gamepad.update()

            sleep = interval - (time.perf_counter() - t0)
            if sleep > 0:
                time.sleep(sleep)

    # ── public API ────────────────────────────────────────

    def start(self):
        if self.active:
            return
        self.gamepad     = vg.VX360Gamepad()
        self.active      = True
        self._mouse_x    = None
        self._mouse_y    = None
        self._mouse_dx   = 0.0
        self._mouse_dy   = 0.0
        self.pressed.clear()
        self._rebuild_reverse()

        self._kb_listener = kb.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        # NOTE: on_move signature is (x, y) — NO dx/dy in pynput
        self._ms_listener = ms.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
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
        self.gamepad = None


# ─────────────────────────────────────────────
# GUI
# ─────────────────────────────────────────────

BUTTON_GROUPS = [
    ("D-Pad",              ["DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"]),
    ("Face Buttons",       ["BTN_A", "BTN_B", "BTN_X", "BTN_Y"]),
    ("Special",            ["BTN_START", "BTN_BACK", "BTN_LTHUMB", "BTN_RTHUMB"]),
    ("Shoulders & Triggers", ["BTN_LB", "BTN_RB", "TRIGGER_LT", "TRIGGER_RT"]),
    ("Left Stick Keys",    ["LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT"]),
]

FRIENDLY = {
    "DPAD_UP":    "D-Pad Up",
    "DPAD_DOWN":  "D-Pad Down",
    "DPAD_LEFT":  "D-Pad Left",
    "DPAD_RIGHT": "D-Pad Right",
    "BTN_A":      "A (Cross) - Jump",
    "BTN_B":      "B (Circle) - Crouch",
    "BTN_X":      "X (Square) - Interact/Reload",
    "BTN_Y":      "Y (Triangle) - Weapon Switch",
    "BTN_START":  "Start - Map",
    "BTN_BACK":   "Select/Back - Inventory",
    "BTN_LTHUMB": "L-Stick Click - Sprint",
    "BTN_RTHUMB": "R-Stick Click - Melee",
    "BTN_LB":     "LB (L1) - Tactical",
    "BTN_RB":     "RB (R1) - Ping",
    "TRIGGER_LT": "LT (L2) - ADS",
    "TRIGGER_RT": "RT (R2) - Fire",
    "LS_UP":      "Left Stick Up",
    "LS_DOWN":    "Left Stick Down",
    "LS_LEFT":    "Left Stick Left",
    "LS_RIGHT":   "Left Stick Right",
}

# Tkinter button number → pynput-style name
TK_MOUSE_BTN = {
    1: "buttonleft",
    2: "buttonmiddle",
    3: "buttonright",
}

BG    = "#1e1e2e"
CARD  = "#2a2a3e"
FG    = "#cdd6f4"
ACC   = "#89b4fa"
MUTED = "#6c7086"
RED   = "#f38ba8"
GREEN = "#a6e3a1"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KeyPad Emulator")
        self.configure(bg=BG)
        self.resizable(True, True)

        self.key_map       = dict(DEFAULT_MAP)
        self.emulator      = None
        self.active        = False
        self.listening_for = None
        self.entry_vars    = {}
        self.entry_widgets = {}
        self._extra_ms     = None

        self._build_ui()
        # Size: wide enough for all 5 columns, tall enough to show all rows
        self.geometry("1150x560")
        self.minsize(900, 480)

    # ── UI ────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ──
        hdr = tk.Frame(self, bg="#11111b", pady=10, padx=20)
        hdr.pack(fill=tk.X)

        tk.Label(hdr, text="KeyPad Emulator",
                 font=("Segoe UI", 14, "bold"), bg="#11111b", fg=FG).pack(side=tk.LEFT)

        self.status_lbl = tk.Label(hdr, text="● Inactive",
                                   font=("Segoe UI", 11), bg="#11111b", fg=MUTED)
        self.status_lbl.pack(side=tk.LEFT, padx=20)

        self.toggle_btn = tk.Button(
            hdr, text="Activate", font=("Segoe UI", 10, "bold"),
            bg=RED, fg="#1e1e2e", relief=tk.FLAT, padx=14, pady=5,
            cursor="hand2", command=self.toggle_emulator)
        self.toggle_btn.pack(side=tk.RIGHT)

        # ── Notebook ──
        nb_wrap = tk.Frame(self, bg=BG, padx=14, pady=10)
        nb_wrap.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook",     background=BG,   borderwidth=0)
        style.configure("TNotebook.Tab", background=CARD, foreground=FG,
                        padding=[12, 5], font=("Segoe UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", "#45475a")],
                  foreground=[("selected", FG)])
        style.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG,
                        borderwidth=0, arrowcolor=FG)

        nb = ttk.Notebook(nb_wrap)
        nb.pack(fill=tk.BOTH, expand=True)

        # ── Mapping tab ──
        map_outer = tk.Frame(nb, bg=BG)
        nb.add(map_outer, text="Button Mapping")

        canvas = tk.Canvas(map_outer, bg=BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(map_outer, orient="vertical", command=canvas.yview)
        hscroll = ttk.Scrollbar(map_outer, orient="horizontal", command=canvas.xview)

        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)

        hscroll.pack(side=tk.BOTTOM, fill=tk.X)
        vscroll.pack(side=tk.RIGHT,  fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Mouse-wheel scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # Build columns
        for col, (group_name, actions) in enumerate(BUTTON_GROUPS):
            grp = tk.LabelFrame(
                inner, text=f"  {group_name}  ",
                font=("Segoe UI", 10, "bold"),
                bg=CARD, fg=ACC, bd=1, relief=tk.GROOVE,
                padx=12, pady=10, labelanchor="nw",
            )
            grp.grid(row=0, column=col, padx=8, pady=10, sticky="n")

            for action in actions:
                row_f = tk.Frame(grp, bg=CARD)
                row_f.pack(fill=tk.X, pady=4)

                tk.Label(
                    row_f, text=FRIENDLY[action],
                    font=("Segoe UI", 10), bg=CARD, fg=FG,
                    width=24, anchor="w",
                ).pack(side=tk.LEFT)

                var = tk.StringVar(value=self.key_map[action])
                self.entry_vars[action] = var

                chip = tk.Button(
                    row_f, textvariable=var,
                    font=("Segoe UI Mono", 10, "bold"),
                    bg="#313244", fg=ACC, relief=tk.FLAT,
                    width=13, cursor="hand2",
                    command=lambda a=action: self.start_listen(a),
                )
                chip.pack(side=tk.LEFT, padx=6)
                self.entry_widgets[action] = chip

        # ── Settings tab ──
        settings_frame = tk.Frame(nb, bg=BG, padx=24, pady=18)
        nb.add(settings_frame, text="Settings")

        def slider_row(parent, label, from_, to, initial, fmt):
            f = tk.Frame(parent, bg=BG)
            f.pack(fill=tk.X, pady=10)
            tk.Label(f, text=label, font=("Segoe UI", 10),
                     bg=BG, fg=FG, width=22, anchor="w").pack(side=tk.LEFT)
            val_lbl = tk.Label(f, text=fmt(initial),
                               font=("Segoe UI", 10, "bold"), bg=BG, fg=ACC, width=6)
            val_lbl.pack(side=tk.RIGHT)
            sc = ttk.Scale(f, from_=from_, to=to, orient=tk.HORIZONTAL,
                           command=lambda v, l=val_lbl, f=fmt: l.config(text=f(float(v))))
            sc.set(initial)
            sc.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
            return sc

        self.ms_scale = slider_row(settings_frame, "Mouse Sensitivity",
                                   0.1, 2.0, 0.5, lambda v: f"{v:.2f}")
        self.ls_scale = slider_row(settings_frame, "Left Stick Speed",
                                   0.1, 2.0, 1.0, lambda v: f"{v:.2f}")
        self.dz_scale = slider_row(settings_frame, "Dead Zone",
                                   0.0, 0.5, 0.1, lambda v: f"{int(v*100)}%")

        tk.Label(settings_frame,
                 text="Right stick is always controlled by mouse movement.",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(18, 0))

        # ── Setup tab ──
        inst_frame = tk.Frame(nb, bg=BG, padx=24, pady=18)
        nb.add(inst_frame, text="Setup")

        instructions = (
            "INSTALLATION\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "1. Install ViGEmBus driver\n"
            "   → https://github.com/ViGEm/ViGEmBus/releases\n"
            "   (Download & run the latest .exe installer)\n\n"
            "2. Install Python packages\n"
            "   > pip install vgamepad pynput\n\n"
            "3. Run this script\n"
            "   > python keypad_emulator.py\n\n"
            "HOW TO USE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Click 'Activate' — Windows detects an Xbox 360 controller.\n"
            "• To remap: click a key chip, then press any key or mouse button.\n"
            "  Side buttons (X1/X2 on G304 etc.) are supported.\n"
            "• Press Escape to cancel a remap.\n"
            "• Mouse movement  → Right Stick (camera / aim)\n"
            "• Left click      → RT / Fire  (default)\n"
            "• Right click     → LT / ADS   (default)\n\n"
            "NOTES\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• Works with Steam, Epic Games, Xbox Game Pass, etc.\n"
            "• Run as Administrator if the virtual controller is not detected.\n"
            "• Changes to mappings take effect immediately, even while active.\n"
        )
        tk.Label(inst_frame, text=instructions, font=("Consolas", 10),
                 bg=BG, fg=FG, justify=tk.LEFT).pack(anchor="w")

        # ── Footer ──
        foot = tk.Frame(self, bg="#11111b", pady=6, padx=16)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(
            foot,
            text="Click any key chip to remap  •  Mouse → Right Stick  "
                 "•  LClick = Fire (RT)   RClick = ADS (LT)",
            font=("Segoe UI", 9), bg="#11111b", fg=MUTED,
        ).pack(side=tk.LEFT)

    # ── Remap logic ───────────────────────────────────────

    def start_listen(self, action):
        self._cancel_listen(silent=True)
        self.listening_for = action

        w = self.entry_widgets[action]
        self.entry_vars[action].set("Press…")
        w.config(bg="#45475a", fg=RED)

        # Keyboard
        self.bind("<KeyPress>", self._capture_key)

        # Standard mouse buttons via Tkinter (delayed to skip the chip click)
        self.after(150, self._bind_tk_mouse)

        # Side buttons (X1, X2, …) via pynput
        def _pynput_click(x, y, btn, pressed):
            if not pressed or not self.listening_for:
                return False
            name = str(btn).lower().replace("button.", "button")
            self.after(0, lambda n=name: self._apply_capture(n))
            return False

        self._extra_ms = ms.Listener(on_click=_pynput_click)
        self._extra_ms.start()

    def _bind_tk_mouse(self):
        if not self.listening_for:
            return
        self.bind("<Button-1>", self._capture_tk_mouse)
        self.bind("<Button-2>", self._capture_tk_mouse)
        self.bind("<Button-3>", self._capture_tk_mouse)

    def _capture_key(self, event):
        if not self.listening_for:
            return
        if event.keysym.lower() == "escape":
            self._cancel_listen()
            return
        self._apply_capture(event.keysym.lower())

    def _capture_tk_mouse(self, event):
        if not self.listening_for:
            return
        w = self.entry_widgets.get(self.listening_for)
        if event.widget is w:
            return
        self._apply_capture(TK_MOUSE_BTN.get(event.num, f"button{event.num}"))

    def _apply_capture(self, key_name):
        action = self.listening_for
        if not action:
            return
        self.key_map[action] = key_name
        self.entry_vars[action].set(key_name)
        self.entry_widgets[action].config(bg="#313244", fg=ACC)
        self._cleanup_listeners()
        self.listening_for = None
        if self.emulator:
            self.emulator.key_map = dict(self.key_map)
            self.emulator._rebuild_reverse()

    def _cancel_listen(self, silent=False):
        action = self.listening_for
        if action:
            self.entry_vars[action].set(self.key_map[action])
            self.entry_widgets[action].config(bg="#313244", fg=ACC)
        self._cleanup_listeners()
        self.listening_for = None

    def _cleanup_listeners(self):
        self.unbind("<KeyPress>")
        self.unbind("<Button-1>")
        self.unbind("<Button-2>")
        self.unbind("<Button-3>")
        if self._extra_ms:
            try:
                self._extra_ms.stop()
            except Exception:
                pass
            self._extra_ms = None

    # ── Activate / Deactivate ─────────────────────────────

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
                self.active   = True
                self.status_lbl.config(
                    text="● Active — Xbox 360 controller connected", fg=GREEN)
                self.toggle_btn.config(text="Deactivate", bg="#313244", fg=FG)
            except Exception as ex:
                messagebox.showerror(
                    "Error",
                    f"Could not start emulator:\n{ex}\n\n"
                    "Make sure ViGEmBus driver is installed.",
                )
        else:
            if self.emulator:
                self.emulator.stop()
                self.emulator = None
            self.active = False
            self.status_lbl.config(text="● Inactive", fg=MUTED)
            self.toggle_btn.config(text="Activate", bg=RED, fg="#1e1e2e")

    def on_close(self):
        if self.emulator:
            self.emulator.stop()
        self._cleanup_listeners()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
