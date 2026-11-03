"""
Claude 사용량 바탕화면 위젯 (Windows)

- 휴지통·크롬 아이콘처럼 바탕화면 아이콘 칸에 딱 맞게 붙습니다 (2×2, 2×3, 3×2 고정 크기).
- 한 번 클릭하면 Claude 앱이 열립니다 (앱이 없으면 claude.ai를 브라우저로 엽니다).
- 드래그하면 옮길 수 있고, 놓으면 가장 가까운 칸에 붙습니다.
- 다른 창 뒤에 깔리고, 작업 표시줄/Alt+Tab에는 나타나지 않습니다.
- 1분마다 사용량을 가져오며, 숫자와 막대만 바뀌어 깜빡이지 않습니다.
- 오른쪽 클릭 메뉴: 새로고침 / 크기 / 클릭하면 열기 / 자동 실행 / 세션 키 / 종료

※ claude.ai 설정의 사용량 페이지가 쓰는 비공식 엔드포인트를 호출합니다.
  Anthropic이 구조를 바꾸면 동작이 멈출 수 있습니다.
※ sessionKey는 로그인 정보와 같으니 다른 사람과 공유하지 마세요.
"""

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

try:
    import keyring
except Exception:
    keyring = None

HTTP_ERR = None
try:
    from curl_cffi import requests as http
    HTTP_KW = {"impersonate": "chrome"}
    HEADERS = {"Accept": "application/json"}
except Exception as e:
    HTTP_ERR = e
    try:
        import requests as http
        HTTP_KW = {}
        HEADERS = {
            "Accept": "application/json",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/130.0 Safari/537.36"),
        }
        HTTP_ERR = None
    except Exception:
        http = None

# --- 설정 ---------------------------------------------------------------------
APP = "ClaudeUsageWidget"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
PID_PATH = os.path.join(CONFIG_DIR, "pid.txt")
LOG_PATH = os.path.join(CONFIG_DIR, "error.log")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
API = "https://claude.ai/api"
REFRESH_SEC = 60

SIZES = {  # 이름: (가로 칸, 세로 칸)
    "2x2": (2, 2),
    "2x3": (2, 3),
    "3x2": (3, 2),
}
SIZE_LABELS = {"2x2": "가로 2 × 세로 2", "2x3": "가로 2 × 세로 3",
               "3x2": "가로 3 × 세로 2"}

BUCKETS = [  # 중요도 순. 칸이 모자라면 뒤쪽부터 생략
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


# --- Win32 --------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
HWND, UINT, DWORD = wintypes.HWND, wintypes.UINT, wintypes.DWORD
LONG_PTR = ctypes.c_ssize_t

_GetWL = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
_SetWL = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
_GetWL.restype, _GetWL.argtypes = LONG_PTR, [HWND, ctypes.c_int]
_SetWL.restype, _SetWL.argtypes = LONG_PTR, [HWND, ctypes.c_int, LONG_PTR]
user32.GetParent.restype, user32.GetParent.argtypes = HWND, [HWND]
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
user32.SystemParametersInfoW.argtypes = [UINT, UINT, ctypes.c_void_p, UINT]
user32.AllowSetForegroundWindow.argtypes = [DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [DWORD, wintypes.BOOL, DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                    ctypes.c_size_t, DWORD, DWORD]
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                   ctypes.c_size_t, DWORD]
for _fn in (kernel32.WriteProcessMemory, kernel32.ReadProcessMemory):
    _fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

GWL_EXSTYLE, GWLP_HWNDPARENT = -20, -8
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x80, 0x40000, 0x08000000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010
HWND_BOTTOM = HWND(1)


def desktop_host():
    """바탕화면 아이콘을 담고 있는 창(Progman 또는 WorkerW)."""
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
    """바탕화면(탐색기 프로세스)의 아이콘 위치를 읽음. code 0=전체, 1=아이콘."""
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
    """(칸 너비, 칸 높이, 0번 열 왼쪽 x, 0번 행 위쪽 y) — 실제 바탕화면 아이콘 기준."""
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
    # 바탕화면 정보를 못 읽었을 때의 대략값
    w, h = ctypes.c_int(), ctypes.c_int()
    user32.SystemParametersInfoW(0x000D, 0, ctypes.byref(w), 0)
    user32.SystemParametersInfoW(0x0018, 0, ctypes.byref(h), 0)
    r = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return max(w.value, 60), max(h.value, 60), r.left, r.top


def pin_to_desktop(hwnd, host):
    ex = _GetWL(hwnd, GWL_EXSTYLE)
    ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
    _SetWL(hwnd, GWL_EXSTYLE, ex)
    if host:
        _SetWL(hwnd, GWLP_HWNDPARENT, host)
    send_to_bottom(hwnd)
    try:
        pref = ctypes.c_int(2)  # Windows 11 둥근 모서리
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            HWND(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def send_to_bottom(hwnd):
    user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_FLAGS)


def virtual_screen():
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def already_running():
    kernel32.CreateMutexW(None, False, "Local\\" + APP)
    return ctypes.get_last_error() == 183


# --- Claude 열기 --------------------------------------------------------------
def open_claude(target):
    user32.AllowSetForegroundWindow(0xFFFFFFFF)  # 새 창이 앞으로 오도록 허용
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


# --- 저장소 -------------------------------------------------------------------
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
    if keyring:
        return keyring.get_password(APP, "sessionKey")
    return load_config().get("sessionKey")


def set_session_key(value):
    if keyring:
        keyring.set_password(APP, "sessionKey", value)
    else:
        cfg = load_config()
        cfg["sessionKey"] = value
        save_config(cfg)


# --- 자동 실행 ----------------------------------------------------------------
def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
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


# --- API ----------------------------------------------------------------------
class AuthError(Exception):
    pass


class UsageClient:
    def __init__(self, session_key):
        self.session_key = session_key
        self.org_id = None

    def _get(self, path):
        r = http.get(API + path, headers=HEADERS,
                     cookies={"sessionKey": self.session_key},
                     timeout=15, **HTTP_KW)
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


# --- 표시 형식 ----------------------------------------------------------------
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


# --- 위젯 ---------------------------------------------------------------------
class Widget:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.busy = False
        self.last = None
        self.status = ""
        self.rows = {}
        self.visible = None
        self.press = None

        self.root = tk.Tk()
        self.root.title("Claude 사용량")
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=C["bg"], cursor="hand2")
        self.root.report_callback_exception = self._on_error

        if http is None:
            raise RuntimeError(f"통신 모듈을 불러오지 못했습니다: {HTTP_ERR}")

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

        self.body = tk.Frame(self.root, bg=C["bg"])
        self.body.place(relx=0.5, rely=0.5, anchor="center")
        self.header = tk.Label(self.body, text="Claude", bg=C["bg"],
                               fg=C["muted"], font=(FONT, 8), anchor="w")
        self.placeholder = tk.Label(self.body, text="불러오는 중…", bg=C["bg"],
                                    fg=C["muted"], font=(FONT, 9), anchor="w")
        self.footer = tk.Label(self.body, bg=C["bg"], fg=C["muted"],
                               font=(FONT, 7), anchor="w", justify="left")

        self.grid = icon_grid()
        self.x, self.y = self.cfg.get("x"), self.cfg.get("y")
        if self.x is None:
            self.x = self.grid[2] + self.grid[0] * 2
            self.y = self.grid[3]
        self.apply_size()

        self.size_var = tk.StringVar(value=self.size_name)
        self.target_var = tk.StringVar(value=self.target)
        self.auto_var = tk.BooleanVar(value=autostart_enabled())
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="지금 새로고침", command=self.refresh)
        size_menu = tk.Menu(self.menu, tearoff=0)
        for name in SIZES:
            size_menu.add_radiobutton(label=SIZE_LABELS[name], value=name,
                                      variable=self.size_var,
                                      command=self.change_size)
        self.menu.add_cascade(label="크기", menu=size_menu)
        open_menu = tk.Menu(self.menu, tearoff=0)
        open_menu.add_radiobutton(label="Claude 앱", value="app",
                                  variable=self.target_var,
                                  command=self.change_target)
        open_menu.add_radiobutton(label="claude.ai (브라우저)", value="web",
                                  variable=self.target_var,
                                  command=self.change_target)
        self.menu.add_cascade(label="클릭하면 열기", menu=open_menu)
        self.menu.add_checkbutton(label="Windows 시작 시 자동 실행",
                                  variable=self.auto_var,
                                  command=lambda: set_autostart(self.auto_var.get()))
        self.menu.add_command(label="세션 키 바꾸기", command=self.change_key)
        self.menu.add_separator()
        self.menu.add_command(label="종료", command=self.root.destroy)

        self.root.bind_all("<ButtonPress-1>", self._press)
        self.root.bind_all("<B1-Motion>", self._motion)
        self.root.bind_all("<ButtonRelease-1>", self._release)
        self.root.bind_all("<Button-3>", self._popup)

        self.render()
        self.root.deiconify()
        self.root.update()
        self.hwnd = user32.GetParent(self.root.winfo_id())
        self.host = desktop_host()
        pin_to_desktop(self.hwnd, self.host)

        self.schedule()
        self.poll()
        self.tick()
        self.keep_bottom()
        self.root.mainloop()

    def _on_error(self, *args):
        log_error("".join(traceback.format_exception(*args)))

    # 키 입력
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

    # 크기·칸 맞추기
    def apply_size(self):
        cx, cy, _, _ = self.grid
        cols, rows = SIZES[self.size_name]
        self.m = max(3, cx // 16)
        self.width = cols * cx - 2 * self.m
        self.height = rows * cy - self.m
        self.padx = max(8, cx // 9)
        self.pady = max(6, cy // 14)
        self.inner_w = self.width - 2 * self.padx
        self.small = cols * rows <= 4
        for lbl in (self.footer, self.placeholder):
            lbl.config(wraplength=self.inner_w)
        for r in self.rows.values():
            self._style_row(r)
        self.x, self.y = self.snap(self.x, self.y)
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")
        self.cfg.update(size=self.size_name, x=self.x, y=self.y)
        save_config(self.cfg)
        if self.visible is not None:
            self._repack(self.visible)

    def change_size(self):
        self.size_name = self.size_var.get()
        self.apply_size()
        self.render()

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

    # 갱신
    def schedule(self):
        self.refresh()
        self.root.after(REFRESH_SEC * 1000, self.schedule)

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            self.q.put(("ok", self.client.fetch()))
        except AuthError:
            self.q.put(("auth", None))
        except Exception as e:
            self.q.put(("err", str(e)))

    def poll(self):
        try:
            kind, data = self.q.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            now = datetime.now().strftime("%H:%M")
            if kind == "ok":
                self.last = data
                self.status = f"{now} 업데이트"
            elif kind == "auth":
                self.status = "세션 키 만료 · 우클릭 → 세션 키 바꾸기"
            else:
                self.status = f"{now} 불러오기 실패"
                log_error(data)
            self.render()
        self.root.after(300, self.poll)

    def tick(self):
        self.render()
        self.root.after(30_000, self.tick)

    def keep_bottom(self):
        host = desktop_host()
        if host and host != self.host:  # 탐색기가 다시 시작된 경우
            self.host = host
            pin_to_desktop(self.hwnd, host)
        send_to_bottom(self.hwnd)
        self.root.after(3000, self.keep_bottom)

    # 그리기: 한 번 만들고 이후엔 글자·막대만 바꿈
    def _make_row(self, key, title):
        f = tk.Frame(self.body, bg=C["bg"])
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

    def render(self):
        data = self.last or {}
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
        self.footer.config(text=self.status)
        if visible != self.visible:
            self._repack(visible)

    def _repack(self, visible):
        """정해진 칸 안에 들어가는 만큼만 표시 (넘치면 덜 중요한 것부터 생략)."""
        self.visible = visible
        avail = self.height - 2 * self.pady
        show = list(visible)
        with_footer = True
        while True:
            for w in self.body.winfo_children():
                w.pack_forget()
            self.header.pack(fill="x", pady=(0, 2))
            if not show:
                self.placeholder.pack(fill="x")
            for i, k in enumerate(show):
                self.rows[k]["frame"].pack(fill="x", pady=(0 if i == 0 else 6, 0))
            if with_footer:
                self.footer.pack(fill="x", pady=(4, 0))
            self.root.update_idletasks()
            if self.body.winfo_reqheight() <= avail:
                break
            if with_footer:
                with_footer = False
            elif len(show) > 1:
                show.pop()
            else:
                break

    # 클릭 = Claude 열기, 드래그 = 이동
    def _press(self, e):
        self.press = (e.x_root, e.y_root,
                      e.x_root - self.root.winfo_x(),
                      e.y_root - self.root.winfo_y(), False)

    def _motion(self, e):
        if not self.press:
            return
        sx, sy, dx, dy, moved = self.press
        if not moved and abs(e.x_root - sx) + abs(e.y_root - sy) < 6:
            return
        self.press = (sx, sy, dx, dy, True)
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _release(self, _e):
        if not self.press:
            return
        moved = self.press[4]
        self.press = None
        if moved:
            self.grid = icon_grid()
            self.x, self.y = self.snap(self.root.winfo_x(), self.root.winfo_y())
            self.root.geometry(f"+{self.x}+{self.y}")
            self.cfg["x"], self.cfg["y"] = self.x, self.y
            save_config(self.cfg)
        else:
            open_claude(self.target)
        send_to_bottom(self.hwnd)

    def _popup(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if already_running():
        sys.exit(0)
    try:
        Widget()
    except Exception as exc:
        log_error(traceback.format_exc())
        messagebox.showerror("Claude 사용량 위젯", str(exc))
