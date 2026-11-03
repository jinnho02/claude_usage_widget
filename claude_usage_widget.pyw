import ctypes
import json
import math
import os
import queue
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
import winreg
from ctypes import wintypes
from datetime import datetime, timezone
from tkinter import messagebox, simpledialog

import keyring
from curl_cffi import requests as http

APP = "ClaudeUsageWidget"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
PID_PATH = os.path.join(CONFIG_DIR, "pid.txt")
LOG_PATH = os.path.join(CONFIG_DIR, "error.log")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
API = "https://claude.ai/api"
REFRESH_SEC = 60
CLAUDE_EXE = "claude.exe"
GAUGE_SIZE = 28
KEY_COLOR = "#FF00FE"

SIZES = {
    "2x2": (2, 2),
    "2x3": (2, 3),
    "3x2": (3, 2),
}
SIZE_LABELS = {"2x2": "가로 2 × 세로 2", "2x3": "가로 2 × 세로 3",
               "3x2": "가로 3 × 세로 2"}

BUCKETS = [
    ("five_hour", "현재 세션"),
    ("seven_day", "주간 전체"),
    ("seven_day_opus", "주간 Opus"),
    ("seven_day_sonnet", "주간 Sonnet"),
]

C = {
    "bg": "#1E2433",
    "track": "#323A4E",
    "text": "#E8ECF4",
    "muted": "#8F99AE",
    "ok": "#5FB3A1",
    "warn": "#E0A84A",
    "high": "#E2645A",
}
FONT = "Malgun Gothic"


def log_error(text):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
    except OSError:
        pass


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
HWND, UINT, DWORD = wintypes.HWND, wintypes.UINT, wintypes.DWORD
LONG_PTR = ctypes.c_ssize_t

_GetWL = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_SetWL = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_GetWL.restype, _GetWL.argtypes = LONG_PTR, [HWND, ctypes.c_int]
_SetWL.restype, _SetWL.argtypes = LONG_PTR, [HWND, ctypes.c_int, LONG_PTR]
user32.GetParent.restype, user32.GetParent.argtypes = HWND, [HWND]
user32.GetWindow.restype, user32.GetWindow.argtypes = HWND, [HWND, UINT]
user32.FindWindowW.restype = HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = HWND
user32.FindWindowExW.argtypes = [HWND, HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, UINT]
user32.SendMessageW.restype = LONG_PTR
user32.SendMessageW.argtypes = [HWND, UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
for _fn in (user32.IsWindow, user32.IsWindowVisible, user32.IsIconic):
    _fn.argtypes = [HWND]
user32.SystemParametersInfoW.argtypes = [UINT, UINT, ctypes.c_void_p, UINT]
user32.AllowSetForegroundWindow.argtypes = [DWORD]
WINEVENTPROC = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, DWORD, HWND,
                                  wintypes.LONG, wintypes.LONG, DWORD, DWORD)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.SetWinEventHook.argtypes = [DWORD, DWORD, wintypes.HMODULE, WINEVENTPROC,
                                   DWORD, DWORD, DWORD]
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [DWORD, wintypes.BOOL, DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(DWORD)]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_size_t, DWORD, DWORD]
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                   ctypes.c_size_t, DWORD]
for _fn in (kernel32.WriteProcessMemory, kernel32.ReadProcessMemory):
    _fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]
dwmapi.DwmSetWindowAttribute.argtypes = [HWND, DWORD, ctypes.c_void_p, DWORD]

GWL_EXSTYLE, GWLP_HWNDPARENT = -20, -8
WS_EX_TOPMOST, WS_EX_TOOLWINDOW = 0x8, 0x80
WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x40000, 0x08000000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010
HWND_BOTTOM = HWND(1)
GW_OWNER, GW_HWNDPREV = 4, 3
EVENT_FOREGROUND, EVENT_MINIMIZE_START, EVENT_MINIMIZE_END = 0x3, 0x16, 0x17
EVENT_LOCATION = 0x800B


def toplevel_hwnd(widget):
    return user32.GetParent(widget.winfo_id())


def make_tool_window(hwnd):
    ex = _GetWL(hwnd, GWL_EXSTYLE)
    _SetWL(hwnd, GWL_EXSTYLE,
           (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)


def round_corners(hwnd):
    try:
        pref = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref),
                                     ctypes.sizeof(pref))
    except Exception:
        pass


def desktop_host():
    progman = user32.FindWindowW("Progman", None)
    if progman and user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None):
        return progman
    found = []

    def cb(h, _):
        if user32.FindWindowExW(h, None, "SHELLDLL_DefView", None):
            found.append(h)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else progman


def desktop_listview():
    host = desktop_host()
    if not host:
        return None
    dv = user32.FindWindowExW(host, None, "SHELLDLL_DefView", None)
    return user32.FindWindowExW(dv, None, "SysListView32", None) if dv else None


def remote_item_rect(lv, index, code):
    pid = DWORD()
    user32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
    hp = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)
    if not hp:
        return None
    try:
        rc = wintypes.RECT(code, 0, 0, 0)
        size = ctypes.sizeof(rc)
        addr = kernel32.VirtualAllocEx(hp, None, size, 0x3000, 0x04)
        if not addr:
            return None
        try:
            n = ctypes.c_size_t()
            if not kernel32.WriteProcessMemory(hp, addr, ctypes.byref(rc), size,
                                               ctypes.byref(n)):
                return None
            if not user32.SendMessageW(lv, 0x100E, index, addr):  # LVM_GETITEMRECT
                return None
            if not kernel32.ReadProcessMemory(hp, addr, ctypes.byref(rc), size,
                                              ctypes.byref(n)):
                return None
            return rc.left, rc.top, rc.right, rc.bottom
        finally:
            kernel32.VirtualFreeEx(hp, addr, 0, 0x8000)
    finally:
        kernel32.CloseHandle(hp)


def icon_grid():
    lv = desktop_listview()
    if lv:
        try:
            sp = user32.SendMessageW(lv, 0x1033, 0, 0)  # LVM_GETITEMSPACING
            cx, cy = sp & 0xFFFF, (sp >> 16) & 0xFFFF
            origin = wintypes.POINT(0, 0)
            user32.ClientToScreen(lv, ctypes.byref(origin))
            count = user32.SendMessageW(lv, 0x1004, 0, 0)  # LVM_GETITEMCOUNT
            if cx >= 20 and cy >= 20:
                if count > 0:
                    icon = remote_item_rect(lv, 0, 1)
                    bounds = remote_item_rect(lv, 0, 0)
                    if icon and bounds:
                        center = (icon[0] + icon[2]) / 2
                        return (cx, cy, origin.x + round(center - cx / 2),
                                origin.y + bounds[1])
                return cx, cy, origin.x, origin.y
        except Exception:
            log_error(traceback.format_exc())
    w, h = ctypes.c_int(), ctypes.c_int()
    user32.SystemParametersInfoW(0x000D, 0, ctypes.byref(w), 0)
    user32.SystemParametersInfoW(0x0018, 0, ctypes.byref(h), 0)
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return max(w.value, 60), max(h.value, 60), r.left, r.top


def pin_to_desktop(hwnd, host):
    make_tool_window(hwnd)
    if host:
        _SetWL(hwnd, GWLP_HWNDPARENT, host)
    send_to_bottom(hwnd)
    round_corners(hwnd)


def send_to_bottom(hwnd):
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_FLAGS)


def virtual_screen():
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def already_running():
    kernel32.CreateMutexW(None, False, "Local\\" + APP)
    return ctypes.get_last_error() == 183


_proc_names = {}


def process_name(pid):
    if pid in _proc_names:
        return _proc_names[pid]
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                name = os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(h)
    if len(_proc_names) > 500:
        _proc_names.clear()
    _proc_names[pid] = name
    return name


def window_pid(hwnd):
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_rect(hwnd):
    r = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)):
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def is_cloaked(hwnd):
    v = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(v), ctypes.sizeof(v))
    return v.value != 0


def dpi_scale(hwnd):
    f = getattr(user32, "GetDpiForWindow", None)
    if f:
        f.restype, f.argtypes = UINT, [HWND]
    d = f(hwnd) if f else 96
    return (d or 96) / 96


def find_claude_window():
    best = [None, 0]

    def cb(h, _):
        if not user32.IsWindowVisible(h) or user32.GetWindow(h, GW_OWNER):
            return True
        if _GetWL(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        if process_name(window_pid(h)) != CLAUDE_EXE:
            return True
        l, t, r, b = window_rect(h)
        area = (r - l) * (b - t)
        if area > best[1]:
            best[0], best[1] = h, area
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return best[0]


def place_above(hwnds, target):
    ours = set(hwnds)
    anchor = user32.GetWindow(target, GW_HWNDPREV)
    while anchor and anchor in ours:
        anchor = user32.GetWindow(anchor, GW_HWNDPREV)
    if anchor and _GetWL(anchor, GWL_EXSTYLE) & WS_EX_TOPMOST:
        anchor = None
    after = HWND(anchor) if anchor else HWND(0)
    for h in hwnds:
        user32.SetWindowPos(h, after, 0, 0, 0, 0, SWP_FLAGS)


def open_claude(target):
    user32.AllowSetForegroundWindow(0xFFFFFFFF)
    if target == "app":
        local = os.environ.get("LOCALAPPDATA", "")
        for path in (os.path.join(local, "AnthropicClaude", "claude.exe"),
                     os.path.join(local, "Programs", "Claude", "Claude.exe"),
                     os.path.join(local, "Programs", "claude", "Claude.exe")):
            if os.path.exists(path):
                os.startfile(path)
                return
        try:
            os.startfile("claude://")
            return
        except OSError:
            pass
    webbrowser.open("https://claude.ai")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get_session_key():
    return keyring.get_password(APP, "sessionKey")


def set_session_key(value):
    keyring.set_password(APP, "sessionKey", value)


def launch_command():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
        return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except FileNotFoundError:
                pass


class AuthError(Exception):
    pass


class UsageClient:
    def __init__(self, session_key):
        self.session_key = session_key
        self.org_id = None

    def _get(self, path):
        r = http.get(API + path, headers={"Accept": "application/json"},
                     cookies={"sessionKey": self.session_key},
                     timeout=15, impersonate="chrome")
        if r.status_code in (401, 403):
            raise AuthError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()

    def fetch(self):
        if self.org_id:
            return self._get(f"/organizations/{self.org_id}/usage")
        for org in self._get("/organizations"):
            try:
                data = self._get(f"/organizations/{org['uuid']}/usage")
            except AuthError:
                raise
            except Exception:
                continue
            if isinstance(data, dict) and data.get("five_hour"):
                self.org_id = org["uuid"]
                return data
        raise RuntimeError("사용량 정보가 있는 조직을 찾지 못했습니다")


def until_reset(iso):
    if not iso:
        return ""
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    sec = int((t - datetime.now(timezone.utc)).total_seconds())
    if sec <= 0:
        return "곧 초기화"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}일 {h}시간 후 초기화"
    if h:
        return f"{h}시간 {m}분 후 초기화"
    return f"{m}분 후 초기화"


def level_color(pct):
    if pct >= 85:
        return C["high"]
    if pct >= 60:
        return C["warn"]
    return C["ok"]


class UsageView:
    def __init__(self, master):
        self.frame = tk.Frame(master, bg=C["bg"])
        self.frame.place(relx=0.5, rely=0.5, anchor="center")
        self.header = tk.Label(self.frame, text="Claude", bg=C["bg"],
                               fg=C["muted"], font=(FONT, 8), anchor="w")
        self.placeholder = tk.Label(self.frame, text="불러오는 중…", bg=C["bg"],
                                    fg=C["muted"], font=(FONT, 9), anchor="w")
        self.footer = tk.Label(self.frame, bg=C["bg"], fg=C["muted"],
                               font=(FONT, 7), anchor="w", justify="left")
        self.rows = {}
        self.visible = None
        self.height = self.pady = 0
        self.inner_w = 100
        self.small = True

    def set_size(self, width, height, padx, pady, small):
        self.height, self.pady, self.small = height, pady, small
        self.inner_w = width - 2 * padx
        for lbl in (self.footer, self.placeholder):
            lbl.config(wraplength=self.inner_w)
        for r in self.rows.values():
            self._style_row(r)
        if self.visible is not None:
            self._repack(self.visible)

    def _make_row(self, key, title):
        f = tk.Frame(self.frame, bg=C["bg"])
        head = tk.Frame(f, bg=C["bg"])
        head.pack(fill="x")
        name = tk.Label(head, text=title, bg=C["bg"], fg=C["text"])
        name.pack(side="left", anchor="s")
        pct = tk.Label(head, bg=C["bg"])
        pct.pack(side="right", anchor="s")
        cv = tk.Canvas(f, height=6, bg=C["bg"], highlightthickness=0)
        cv.pack(anchor="w", pady=(2, 2))
        track = cv.create_rectangle(0, 0, 0, 0, fill=C["track"], width=0)
        fill = cv.create_rectangle(0, 0, 0, 0, fill=C["ok"], width=0)
        reset = tk.Label(f, bg=C["bg"], fg=C["muted"], anchor="w")
        reset.pack(anchor="w")
        r = {"key": key, "frame": f, "name": name, "pct": pct, "cv": cv,
             "track": track, "fill": fill, "reset": reset, "value": 0.0}
        self._style_row(r)
        return r

    def _style_row(self, r):
        big = r["key"] == "five_hour"
        if self.small:
            pct_size = 18 if big else 12
        else:
            pct_size = 22 if big else 14
        h = 7 if big else 5
        r["h"] = h
        r["name"].config(font=(FONT, 9 if big else 8, "bold"))
        r["pct"].config(font=(FONT, pct_size, "bold"))
        r["reset"].config(font=(FONT, 7), wraplength=self.inner_w)
        r["cv"].config(width=self.inner_w, height=h)
        r["cv"].coords(r["track"], 0, 0, self.inner_w, h)
        r["cv"].coords(r["fill"], 0, 0, self.inner_w * r["value"] / 100, h)

    def update(self, data, status):
        visible = []
        for key, title in BUCKETS:
            b = data.get(key)
            if not isinstance(b, dict) or b.get("utilization") is None:
                continue
            visible.append(key)
            if key not in self.rows:
                self.rows[key] = self._make_row(key, title)
            r = self.rows[key]
            pct = max(0.0, min(100.0, float(b["utilization"])))
            r["value"] = pct
            color = level_color(pct)
            r["pct"].config(text=f"{pct:.0f}%", fg=color)
            r["cv"].coords(r["fill"], 0, 0, self.inner_w * pct / 100, r["h"])
            r["cv"].itemconfig(r["fill"], fill=color)
            r["reset"].config(text=until_reset(b.get("resets_at")))
        self.footer.config(text=status)
        self.placeholder.config(text=status or "claude.ai에 연결 중…")
        if visible != self.visible:
            self._repack(visible)

    def _repack(self, visible):
        self.visible = visible
        avail = self.height - 2 * self.pady
        show = list(visible)
        with_footer = True
        while True:
            for w in self.frame.winfo_children():
                w.pack_forget()
            self.header.pack(fill="x", pady=(0, 2))
            if not show:
                self.placeholder.pack(fill="x")
            for i, k in enumerate(show):
                self.rows[k]["frame"].pack(fill="x", pady=(0 if i == 0 else 6, 0))
            if with_footer:
                self.footer.pack(fill="x", pady=(4, 0))
            self.frame.update_idletasks()
            if self.frame.winfo_reqheight() <= avail:
                break
            if with_footer:
                with_footer = False
            elif len(show) > 1:
                show.pop()
            else:
                break


class App:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.busy = False
        self.data = {}
        self.status = ""
        self.press = None
        self.claude = None
        self.loc_hook = None
        self.loc_pid = None
        self.sync_pending = False
        self.panel_shown = False
        self.panel_styled = False
        self.overlay_shown = False
        self.overlay_styled = False
        self.gsize = 0
        self.gpct = None
        self.miss = 0
        self.opos = (0, 0)
        self.busy_since = datetime.now()
        self.logged_sample = False

        self.root = tk.Tk()
        self.root.title("Claude 사용량")
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=C["bg"], cursor="hand2")
        self.root.report_callback_exception = self._on_error

        key = get_session_key() or self.ask_key()
        if not key:
            self.root.destroy()
            return
        self.client = UsageClient(key)

        if not self.cfg.get("autostart_init"):
            set_autostart(True)
            self.cfg["autostart_init"] = True
        elif autostart_enabled():
            set_autostart(True)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(PID_PATH, "w") as f:
            f.write(str(os.getpid()))

        self.size_name = self.cfg.get("size", "2x2")
        if self.size_name not in SIZES:
            self.size_name = "2x2"
        self.target = self.cfg.get("open", "app")
        self.desk_on = self.cfg.get("desktop", True)
        self.gauge_on = self.cfg.get("gauge", True)

        self.desk_view = UsageView(self.root)

        self.panel = tk.Toplevel(self.root, bg=C["bg"])
        self.panel.withdraw()
        self.panel.overrideredirect(True)
        self.panel_view = UsageView(self.panel)

        self.overlay = tk.Toplevel(self.root, bg=KEY_COLOR)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-transparentcolor", KEY_COLOR)
        self.gcv = tk.Canvas(self.overlay, bg=KEY_COLOR, highlightthickness=0,
                             cursor="hand2")
        self.gcv.pack()
        self.overlay.bind("<Enter>", lambda e: self.show_panel())

        self.grid = icon_grid()
        self.x, self.y = self.cfg.get("x"), self.cfg.get("y")
        if self.x is None:
            self.x = self.grid[2] + self.grid[0] * 2
            self.y = self.grid[3]
        self.apply_size()
        self._build_menu()

        self.root.bind_all("<ButtonPress-1>", self._press)
        self.root.bind_all("<B1-Motion>", self._motion)
        self.root.bind_all("<ButtonRelease-1>", self._release)
        self.root.bind_all("<Button-3>", self._popup)

        self.render()
        self.root.update_idletasks()
        self.host = desktop_host()
        self.hwnd = None
        if self.desk_on:
            self.show_desktop()

        self.win_proc = WINEVENTPROC(self._on_win_event)
        self.hooks = [
            user32.SetWinEventHook(EVENT_FOREGROUND, EVENT_FOREGROUND, None,
                                   self.win_proc, 0, 0, 0),
            user32.SetWinEventHook(EVENT_MINIMIZE_START, EVENT_MINIMIZE_END, None,
                                   self.win_proc, 0, 0, 0),
        ]

        self.schedule()
        self.poll()
        self.tick()
        self.keep_bottom()
        self.watch_claude()
        self.watch_events()
        self.root.mainloop()

    def _on_error(self, *args):
        log_error("".join(traceback.format_exception(*args)))

    def _build_menu(self):
        self.size_var = tk.StringVar(value=self.size_name)
        self.target_var = tk.StringVar(value=self.target)
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        self.desk_var = tk.BooleanVar(value=self.desk_on)
        self.gauge_var = tk.BooleanVar(value=self.gauge_on)
        m = self.menu = tk.Menu(self.root, tearoff=0)
        m.add_command(label="지금 새로고침", command=self.refresh)
        m.add_separator()
        m.add_checkbutton(label="바탕화면 위젯 표시", variable=self.desk_var,
                          command=self.toggle_desktop)
        m.add_checkbutton(label="Claude 앱에 계기판 표시", variable=self.gauge_var,
                          command=self.toggle_gauge)
        size_menu = tk.Menu(m, tearoff=0)
        for name in SIZES:
            size_menu.add_radiobutton(label=SIZE_LABELS[name], value=name,
                                      variable=self.size_var,
                                      command=self.change_size)
        m.add_cascade(label="크기", menu=size_menu)
        open_menu = tk.Menu(m, tearoff=0)
        open_menu.add_radiobutton(label="Claude 앱", value="app",
                                  variable=self.target_var,
                                  command=self.change_target)
        open_menu.add_radiobutton(label="claude.ai (브라우저)", value="web",
                                  variable=self.target_var,
                                  command=self.change_target)
        m.add_cascade(label="위젯 클릭하면 열기", menu=open_menu)
        m.add_command(label="계기판 위치 초기화", command=self.reset_gauge_pos)
        m.add_separator()
        m.add_checkbutton(label="Windows 시작 시 자동 실행", variable=self.auto_var,
                          command=lambda: set_autostart(self.auto_var.get()))
        m.add_command(label="세션 키 바꾸기", command=self.change_key)
        m.add_separator()
        m.add_command(label="종료", command=self.quit)

    def quit(self):
        for h in self.hooks + [self.loc_hook]:
            if h:
                user32.UnhookWinEvent(h)
        self.root.destroy()

    def ask_key(self):
        tmp = tk.Toplevel(self.root)
        tmp.withdraw()
        key = simpledialog.askstring(
            "Claude 세션 키",
            "claude.ai에 로그인한 브라우저에서 F12 → Application → Cookies →\n"
            "https://claude.ai 의 sessionKey 값을 붙여 넣으세요.\n\n"
            "이 PC의 Windows 자격 증명 관리자에만 저장됩니다.",
            show="•", parent=tmp)
        tmp.destroy()
        key = (key or "").strip()
        if key:
            set_session_key(key)
        return key or None

    def change_key(self):
        key = self.ask_key()
        if key:
            self.client = UsageClient(key)
            self.refresh()

    def change_target(self):
        self.target = self.target_var.get()
        self.cfg["open"] = self.target
        save_config(self.cfg)

    def toggle_desktop(self):
        if not self.desk_var.get() and not self.gauge_on:
            self.desk_var.set(True)
            return
        self.desk_on = self.desk_var.get()
        self.cfg["desktop"] = self.desk_on
        save_config(self.cfg)
        if self.desk_on:
            self.show_desktop()
        else:
            self.root.withdraw()

    def toggle_gauge(self):
        if not self.gauge_var.get() and not self.desk_on:
            self.gauge_var.set(True)
            return
        self.gauge_on = self.gauge_var.get()
        self.cfg["gauge"] = self.gauge_on
        save_config(self.cfg)
        self.sync()

    def show_desktop(self):
        self.root.deiconify()
        self.root.update()
        self.hwnd = toplevel_hwnd(self.root)
        pin_to_desktop(self.hwnd, self.host)

    def apply_size(self):
        cx, cy, _, _ = self.grid
        cols, rows = SIZES[self.size_name]
        self.m = max(3, cx // 16)
        self.width = cols * cx - 2 * self.m
        self.height = rows * cy - self.m
        padx, pady = max(8, cx // 9), max(6, cy // 14)
        small = cols * rows <= 4
        self.desk_view.set_size(self.width, self.height, padx, pady, small)
        self.panel_view.set_size(self.width, self.height, padx, pady, small)
        self.x, self.y = self.snap(self.x, self.y)
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")
        self.cfg.update(size=self.size_name, x=self.x, y=self.y)
        save_config(self.cfg)

    def change_size(self):
        self.size_name = self.size_var.get()
        self.apply_size()
        self.render()
        if self.panel_shown:
            self.position_panel()

    def snap(self, x, y):
        cx, cy, x0, y0 = self.grid
        vx, vy, vw, vh = virtual_screen()
        k = round((x - self.m - x0) / cx)
        j = round((y - y0) / cy)
        nx, ny = x0 + k * cx + self.m, y0 + j * cy
        while nx + self.width > vx + vw and k > -1000:
            k -= 1
            nx = x0 + k * cx + self.m
        while nx < vx:
            k += 1
            nx = x0 + k * cx + self.m
        while ny + self.height > vy + vh and j > -1000:
            j -= 1
            ny = y0 + j * cy
        while ny < vy:
            j += 1
            ny = y0 + j * cy
        return nx, ny

    def schedule(self):
        self.refresh()
        self.root.after(REFRESH_SEC * 1000, self.schedule)

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.busy_since = datetime.now()
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            self.q.put(("ok", self.client.fetch()))
        except AuthError:
            self.q.put(("auth", None))
        except Exception as e:
            log_error(traceback.format_exc())
            self.q.put(("err", f"{type(e).__name__}: {e}"))

    def poll(self):
        try:
            kind, data = self.q.get_nowait()
        except queue.Empty:
            if self.busy and (datetime.now() - self.busy_since).seconds > 40:
                self.busy = False
                self.status = "claude.ai 응답 없음 · 다시 시도 중"
                log_error("fetch timeout (40s)")
                self.render()
        else:
            self.busy = False
            now = datetime.now().strftime("%H:%M")
            if kind == "ok":
                if not self.logged_sample:
                    log_error("usage sample: " + json.dumps(data)[:1500])
                    self.logged_sample = True
                self.data = data
                self.status = f"{now} 업데이트"
            elif kind == "auth":
                self.status = "세션 키 만료 · 우클릭 → 세션 키 바꾸기"
            else:
                self.status = f"{now} 불러오기 실패 · {data[:40]}"
            self.render()
        self.root.after(300, self.poll)

    def tick(self):
        self.render()
        self.root.after(30_000, self.tick)

    def keep_bottom(self):
        if self.desk_on and self.hwnd:
            host = desktop_host()
            if host and host != self.host:
                self.host = host
                pin_to_desktop(self.hwnd, host)
            send_to_bottom(self.hwnd)
        self.root.after(3000, self.keep_bottom)

    def render(self):
        self.desk_view.update(self.data, self.status)
        self.panel_view.update(self.data, self.status)
        self.draw_gauge()

    def draw_gauge(self, force=False):
        b = self.data.get("five_hour")
        pct = None
        if isinstance(b, dict) and b.get("utilization") is not None:
            pct = max(0.0, min(100.0, float(b["utilization"])))
        if not force and pct == self.gpct:
            return
        self.gpct = pct
        s = self.gsize or GAUGE_SIZE
        cv = self.gcv
        cv.delete("all")
        cv.create_oval(1, 1, s - 1, s - 1, fill=C["bg"], outline=C["track"])
        r = s * 0.30
        cx, cy = s / 2, s / 2 + s * 0.06
        box = (cx - r, cy - r, cx + r, cy + r)
        w = max(2, s // 10)
        cv.create_arc(*box, start=-30, extent=240, style="arc",
                      outline=C["track"], width=w)
        value = pct or 0.0
        if pct:
            cv.create_arc(*box, start=210, extent=-240 * value / 100, style="arc",
                          outline=level_color(value), width=w)
        a = math.radians(210 - 240 * value / 100)
        cv.create_line(cx, cy, cx + r * 0.85 * math.cos(a),
                       cy - r * 0.85 * math.sin(a),
                       fill=C["text"], width=max(1, s // 16))
        d = max(1.5, s / 14)
        cv.create_oval(cx - d, cy - d, cx + d, cy + d, fill=C["text"], outline="")

    def _on_win_event(self, hook, event, hwnd, id_object, id_child, thread, time):
        if event == EVENT_LOCATION and (hwnd != self.claude or id_object != 0):
            return
        self.sync_pending = True

    def watch_events(self):
        if self.sync_pending:
            self.sync()
        self.root.after(30, self.watch_events)

    def watch_claude(self):
        self.sync()
        self.root.after(500, self.watch_claude)

    def _hook_location(self, pid):
        if pid == self.loc_pid:
            return
        if self.loc_hook:
            user32.UnhookWinEvent(self.loc_hook)
        self.loc_hook = user32.SetWinEventHook(EVENT_LOCATION, EVENT_LOCATION,
                                               None, self.win_proc, pid, 0, 0)
        self.loc_pid = pid

    def hide_overlay(self):
        if self.overlay_shown:
            self.overlay.withdraw()
            self.overlay_shown = False
        self.hide_panel()

    def sync(self):
        self.sync_pending = False
        if not self.gauge_on:
            self.hide_overlay()
            return
        h = self.claude
        if not (h and user32.IsWindow(h) and user32.IsWindowVisible(h)):
            h = self.claude = find_claude_window()
        if not h or user32.IsIconic(h) or is_cloaked(h):
            self.hide_overlay()
            return
        self._hook_location(window_pid(h))

        left, top, right, bottom = window_rect(h)
        scale = dpi_scale(h)
        size = round(GAUGE_SIZE * scale)
        if size != self.gsize:
            self.gsize = size
            self.gcv.config(width=size, height=size)
            self.draw_gauge(force=True)
        dx = self.cfg.get("gauge_dx", -round(23 * scale) - size // 2)
        dy = self.cfg.get("gauge_dy", round(46 * scale))
        self.claude_rect = (left, top, right, bottom)

        if not (self.press and self.press[5] is self.overlay):
            self.opos = (right + dx, top + dy)
            geo = f"{size}x{size}+{right + dx}+{top + dy}"
            if self.overlay.geometry() != geo:
                self.overlay.geometry(geo)
        if not self.overlay_shown:
            self.overlay.deiconify()
            self.overlay_shown = True
            if not self.overlay_styled:
                self.overlay.update_idletasks()
                make_tool_window(toplevel_hwnd(self.overlay))
                self.overlay_styled = True
        if self.panel_shown:
            self.position_panel()
        ours = [toplevel_hwnd(self.overlay)]
        if self.panel_shown:
            ours.append(toplevel_hwnd(self.panel))
        place_above(ours, h)

    def reset_gauge_pos(self):
        self.cfg.pop("gauge_dx", None)
        self.cfg.pop("gauge_dy", None)
        save_config(self.cfg)
        self.sync()

    def position_panel(self):
        ox, oy = self.opos
        size = self.gsize or GAUGE_SIZE
        px = ox + size - self.width
        py = oy + size + 6
        left = getattr(self, "claude_rect", (px,))[0]
        px = max(px, left + 4)
        geo = f"{self.width}x{self.height}+{px}+{py}"
        if self.panel.geometry() != geo:
            self.panel.geometry(geo)

    def show_panel(self):
        if self.panel_shown or not self.overlay_shown or self.press:
            return
        self.position_panel()
        self.panel.deiconify()
        self.panel_shown = True
        if not self.panel_styled:
            self.panel.update_idletasks()
            ph = toplevel_hwnd(self.panel)
            make_tool_window(ph)
            round_corners(ph)
            self.panel_styled = True
        if self.claude:
            place_above([toplevel_hwnd(self.overlay), toplevel_hwnd(self.panel)],
                        self.claude)
        self.miss = 0
        self.root.after(120, self._check_hover)

    def hide_panel(self):
        if self.panel_shown:
            self.panel.withdraw()
            self.panel_shown = False

    @staticmethod
    def _inside(w, x, y):
        return (w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width()
                and w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height())

    def _check_hover(self):
        if not self.panel_shown:
            return
        x, y = self.root.winfo_pointerxy()
        if self._inside(self.overlay, x, y) or self._inside(self.panel, x, y):
            self.miss = 0
        else:
            self.miss += 1
        if self.miss >= 3:
            self.hide_panel()
            return
        self.root.after(120, self._check_hover)

    def _top(self, e):
        try:
            return e.widget.winfo_toplevel()
        except Exception:
            return None

    def _press(self, e):
        top = self._top(e)
        if top not in (self.root, self.overlay):
            self.press = None
            return
        if top is self.overlay:
            self.hide_panel()
        self.press = (e.x_root, e.y_root, e.x_root - top.winfo_x(),
                      e.y_root - top.winfo_y(), False, top)

    def _motion(self, e):
        if not self.press:
            return
        sx, sy, dx, dy, moved, top = self.press
        if not moved and abs(e.x_root - sx) + abs(e.y_root - sy) < 6:
            return
        self.press = (sx, sy, dx, dy, True, top)
        nx, ny = e.x_root - dx, e.y_root - dy
        top.geometry(f"+{nx}+{ny}")
        if top is self.overlay:
            self.opos = (nx, ny)

    def _release(self, _e):
        if not self.press:
            return
        moved, top = self.press[4], self.press[5]
        self.press = None
        if top is self.overlay:
            if moved and getattr(self, "claude_rect", None):
                left, t, right, _ = self.claude_rect
                self.cfg["gauge_dx"] = self.opos[0] - right
                self.cfg["gauge_dy"] = self.opos[1] - t
                save_config(self.cfg)
            else:
                self.refresh()
            self.sync()
            return
        if moved:
            self.grid = icon_grid()
            self.x, self.y = self.snap(self.root.winfo_x(), self.root.winfo_y())
            self.root.geometry(f"+{self.x}+{self.y}")
            self.cfg["x"], self.cfg["y"] = self.x, self.y
            save_config(self.cfg)
        else:
            open_claude(self.target)
        if self.hwnd:
            send_to_bottom(self.hwnd)

    def _popup(self, e):
        self.hide_panel()
        self.menu.tk_popup(e.x_root, e.y_root)


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if already_running():
        sys.exit(0)
    try:
        App()
    except Exception as exc:
        log_error(traceback.format_exc())
        messagebox.showerror("Claude 사용량 위젯", str(exc))
