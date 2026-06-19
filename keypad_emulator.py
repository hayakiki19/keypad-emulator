"""
KeyPad Emulator — Keyboard & Mouse -> Virtual Xbox 360 Controller
================================================================
Requires (Windows only):
1. ViGEmBus driver -> https://github.com/ViGEm/ViGEmBus/releases
2. pip install vgamepad pynput

Run as Administrator!

Mouse clicks are captured via a Windows low-level hook (WH_MOUSE_LL)
which works even when a game has exclusive focus.
"""

import sys
import threading
import time
import math
import ctypes
import ctypes.wintypes
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import vgamepad as vg
except ImportError:
    print("ERROR: vgamepad not installed. Run: pip install vgamepad")
    sys.exit(1)

try:
    from pynput import keyboard as kb
except ImportError:
    print("ERROR: pynput not installed. Run: pip install pynput")
    sys.exit(1)

# ─────────────────────────────────────────────
# Windows low-level mouse hook via ctypes
# Captures mouse clicks even inside games
# ─────────────────────────────────────────────

WH_MOUSE_LL   = 14
WM_MOUSEMOVE  = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP   = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP   = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP   = 0x0208

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",      ctypes.wintypes.POINT),
        ("mouseData", ctypes.wintypes.DWORD),
        ("flags",   ctypes.wintypes.DWORD),
        ("time",    ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                               ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

user32  = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LowLevelMouseHook:
    """
    Installs WH_MOUSE_LL — works even when games have focus.
    Calls on_move(x, y) and on_button(name, pressed).
    name is 'button1' (left), 'button2' (right), 'button3' (middle).
    Must run its message loop on its own thread.
    """

    def __init__(self, on_move, on_button):
        self._on_move   = on_move
        self._on_button = on_button
        self._hook      = None
        self._thread    = None
        self._running   = False
        self._hook_ref  = None  # keep reference so GC doesn't collect it

    def _hook_proc(self, nCode, wParam, lParam):
        if nCode >= 0:
            info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            x, y = info.pt.x, info.pt.y

            if wParam == WM_MOUSEMOVE:
                self._on_move(x, y)
            elif wParam == WM_LBUTTONDOWN:
                self._on_button("button1", True)
            elif wParam == WM_LBUTTONUP:
                self._on_button("button1", False)
            elif wParam == WM_RBUTTONDOWN:
                self._on_button("button2", True)
            elif wParam == WM_RBUTTONUP:
                self._on_button("button2", False)
            elif wParam == WM_MBUTTONDOWN:
                self._on_button("button3", True)
            elif wParam == WM_MBUTTONUP:
                self._on_button("button3", False)

        return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

    def _run(self):
        # Install hook on THIS thread — WH_MOUSE_LL must be pumped on same thread
        self._hook_ref = HOOKPROC(self._hook_proc)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._hook_ref,
            kernel32.GetModuleHandleW(None), 0)

        if not self._hook:
            err = ctypes.get_last_error()
            print(f"WARNING: Failed to install mouse hook (error {err}). Run as Administrator!")
            return

        self._thread_id = kernel32.GetCurrentThreadId()

        # Blocking message pump — GetMessageW blocks until a message arrives.
        # This is the correct way; PeekMessage + sleep misses events.
        msg = ctypes.wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break  # WM_QUIT received or error
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def start(self):
        self._running   = True
        self._thread_id = None
        self._thread    = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Wait briefly for thread_id to be set
        for _ in range(50):
            if self._thread_id:
                break
            time.sleep(0.01)

    def stop(self):
        self._running = False
        # Post WM_QUIT to unblock GetMessageW on the hook thread
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT = 0x0012


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
    def __init__(self, key_map, mouse_sensitivity=0.8, ls_sensitivity=1.0,
                 dead_zone=0.0, stick_smoothing=0.37):
        self.key_map           = key_map
        self.mouse_sensitivity = mouse_sensitivity
        self.ls_sensitivity    = ls_sensitivity
        self.dead_zone         = dead_zone
        self.stick_smoothing   = stick_smoothing

        self.gamepad    = None
        self.active     = False
        self.pressed    = set()

        self._mouse_x    = None
        self._mouse_y    = None
        self._mouse_dx   = 0.0
        self._mouse_dy   = 0.0
        self._mouse_lock = threading.Lock()

        self._rs_x_smooth = 0.0
        self._rs_y_smooth = 0.0

        self._kb_listener    = None
        self._mouse_hook     = None
        self._update_thread  = None

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

    # ── callbacks from hook ───────────────────────────────

    def _on_key_press(self, key):
        if not self.active: return
        self.pressed.add(self._norm_kb(key))

    def _on_key_release(self, key):
        if not self.active: return
        self.pressed.discard(self._norm_kb(key))

    def _on_mouse_move(self, x, y):
        if not self.active: return
        with self._mouse_lock:
            if self._mouse_x is not None:
                self._mouse_dx += x - self._mouse_x
                self._mouse_dy += y - self._mouse_y
            self._mouse_x = x
            self._mouse_y = y

    def _on_mouse_button(self, name, pressed):
        # name is 'button1', 'button2', 'button3'
        if not self.active: return
        if pressed:
            self.pressed.add(name)
        else:
            self.pressed.discard(name)

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

        # Keyboard via pynput (works fine for keys)
        self._kb_listener = kb.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release
        )
        self._kb_listener.start()

        # Mouse via Windows low-level hook (works inside games)
        self._mouse_hook = LowLevelMouseHook(
            on_move=self._on_mouse_move,
            on_button=self._on_mouse_button
        )
        self._mouse_hook.start()

        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

    def stop(self):
        self.active = False
        if self._kb_listener:
            self._kb_listener.stop()
        if self._mouse_hook:
            self._mouse_hook.stop()
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

SETUP_TEXT = """INSTALLATION
────────────────────────────────────
1. Install ViGEmBus driver
   https://github.com/ViGEm/ViGEmBus/releases

2. Install Python packages
   pip install vgamepad pynput

3. RIGHT-CLICK the script -> Run as administrator
   (REQUIRED for mouse hook to work in games!)

HOW TO USE
────────────────────────────────────
• Click Activate — Xbox 360 controller appears instantly
• Click any key chip to remap, then press a key or mouse button
• Mouse movement  -> Right Stick (camera / aim)
• Left mouse btn  -> RT (Fire)
• Right mouse btn -> LT (ADS)
• WASD            -> Left Stick (movement)

MOUSE TECH NOTE
────────────────────────────────────
Mouse clicks use a Windows low-level hook (WH_MOUSE_LL).
This captures clicks even inside fullscreen games.
pynput alone cannot do this — that's why admin + this
hook approach is required.

APEX LEGENDS TIPS
────────────────────────────────────
• Controller preset in Apex: Default
• Ultimate = LB + RB (hold Q + G together)
• Run Apex in Borderless Windowed for best results
"""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KeyPad Emulator")
        self.geometry("800x530")
        self.resizable(True, True)
        self.configure(bg="#1a1d2e")

        self.key_map  = dict(DEFAULT_MAP)
        self.emulator = None
        self.active   = False
        self._waiting_for_key = None
        self._remap_kb = None
        self._remap_ms_hook = None

        self._build_ui()

    def _build_ui(self):
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

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background="#1a1d2e", borderwidth=0)
        style.configure("TNotebook.Tab", background="#22253a", foreground="#aaa",
                        padding=[10, 4], font=("Segoe UI", 9))
        style.map("TNotebook.Tab",
                  background=[("selected","#2d3150")],
                  foreground=[("selected","white")])

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(0,4))

        self._map_frame      = tk.Frame(nb, bg="#1a1d2e")
        self._settings_frame = tk.Frame(nb, bg="#1a1d2e")
        self._setup_frame    = tk.Frame(nb, bg="#1a1d2e")

        nb.add(self._map_frame,      text="Button Mapping")
        nb.add(self._settings_frame, text="Settings")
        nb.add(self._setup_frame,    text="Setup")

        self._build_mapping_tab()
        self._build_settings_tab()
        self._build_setup_tab()

        tk.Label(self,
                 text="Click any key chip to remap  •  Mouse -> Right Stick  •  LClick=Fire(RT)  RClick=ADS(LT)",
                 font=("Segoe UI", 8), fg="#555", bg="#1a1d2e"
                 ).pack(side="bottom", pady=(0,4))

    def _build_mapping_tab(self):
        container = tk.Frame(self._map_frame, bg="#1a1d2e")
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, bg="#1a1d2e", highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(canvas, bg="#1a1d2e")
        win_id = canvas.create_window((0,0), window=inner, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(
            win_id, width=e.width))
        inner.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(
            int(-1*(e.delta/120)), "units"))

        self._key_buttons = {}
        cols = 3

        for idx, (group_name, actions) in enumerate(BUTTON_GROUPS):
            col = idx % cols
            row = idx // cols

            grp = tk.LabelFrame(inner, text=group_name,
                                 font=("Segoe UI", 9, "bold"),
                                 fg="#5ab4f5", bg="#22253a",
                                 padx=8, pady=6, relief="groove")
            grp.grid(row=row, column=col, padx=6, pady=6, sticky="nsew")
            inner.columnconfigure(col, weight=1)

            for action in actions:
                rf = tk.Frame(grp, bg="#22253a")
                rf.pack(fill="x", pady=2)

                tk.Label(rf, text=FRIENDLY.get(action, action),
                         font=("Segoe UI", 8), fg="#bbb", bg="#22253a",
                         anchor="w").pack(side="left", fill="x", expand=True)

                key_val = self.key_map.get(action, "-")
                btn = tk.Button(
                    rf, text=key_val,
                    font=("Segoe UI", 9, "bold"),
                    fg="#5ab4f5", bg="#2d3150",
                    relief="flat", padx=6, pady=2,
                    width=9, cursor="hand2",
                    command=lambda a=action: self._start_remap(a)
                )
                btn.pack(side="right")
                self._key_buttons[action] = btn

    def _build_settings_tab(self):
        f = self._settings_frame
        sliders = [
            ("Mouse Sensitivity", "_mouse_sens", 0.1, 3.0, 0.8),
            ("Left Stick Speed",  "_ls_speed",   0.1, 1.0, 1.0),
            ("Dead Zone",         "_dead_zone",  0.0, 0.5, 0.0),
            ("Stick Smoothing",   "_smoothing",  0.0, 0.9, 0.37),
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
            disp = tk.StringVar(value=f"{default:.2f}")
            var.trace_add("write", lambda *a, v=var, d=disp: d.set(f"{v.get():.2f}"))
            tk.Label(f, textvariable=disp, fg="#5ab4f5", bg="#1a1d2e",
                     font=("Segoe UI", 10), width=5
                     ).grid(row=i, column=2, padx=6)

        tk.Label(f,
                 text=("Right stick is always controlled by mouse movement.\n"
                       "Left click = Fire (RT)  •  Right click = ADS (LT)\n\n"
                       "Mouse clicks use WH_MOUSE_LL hook — works inside games."),
                 fg="#666", bg="#1a1d2e", font=("Segoe UI", 9), justify="left"
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
        if self._waiting_for_key: return
        self._waiting_for_key = action
        self._key_buttons[action].config(text="...", fg="#ffcc00")

        # keyboard via pynput
        self._remap_kb = kb.Listener(on_press=self._capture_key)
        self._remap_kb.start()

        # mouse via low-level hook
        self._remap_ms_hook = LowLevelMouseHook(
            on_move=lambda x, y: None,
            on_button=self._capture_mouse_btn
        )
        self._remap_ms_hook.start()

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

    def _capture_mouse_btn(self, name, pressed):
        if not self._waiting_for_key or not pressed: return
        self._apply_remap(name)

    def _apply_remap(self, new_key):
        action = self._waiting_for_key
        self._waiting_for_key = None
        self.key_map[action] = new_key
        self.after(0, lambda: self._key_buttons[action].config(
            text=new_key, fg="#5ab4f5"))
        try:
            self._remap_kb.stop()
        except Exception: pass
        try:
            self._remap_ms_hook.stop()
        except Exception: pass
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
