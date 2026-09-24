# -*- coding: utf-8 -*-
"""Windows 进程集成：取图标、定位文件、打开所在目录。

只用标准库 ctypes 调 shell32 / user32，不依赖 pywin32。

图标取自可执行文件本身（ExtractIconEx），转成 Tk 能用的 PhotoImage。
没有 Pillow 时退回 Tk 的 `iconphoto` 读 .ico；再不行就画一个首字母色块。

定位文件用 `explorer /select,"路径"`——这是唯一不需要额外权限、
且能高亮选中目标文件的系统做法。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

_IS_WIN = sys.platform.startswith("win")

# ---------------------------------------------------------------- Win32 绑定

if _IS_WIN:
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    # HICON 用 c_void_p，不要用 wintypes.HICON —— 后者在部分 ctypes 版本上
    # 不是有效的指针类型，抽出来的句柄会是 0，图标静默拿不到。
    HICON = ctypes.c_void_p

    _shell32.ExtractIconExW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int,
        ctypes.POINTER(HICON), ctypes.POINTER(HICON), wintypes.UINT,
    ]
    _shell32.ExtractIconExW.restype = wintypes.UINT
    _user32.DestroyIcon.argtypes = [HICON]
    _user32.DestroyIcon.restype = wintypes.BOOL

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", ctypes.c_void_p),
            ("hbmColor", ctypes.c_void_p),
        ]

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", wintypes.LONG),
            ("bmWidth", wintypes.LONG),
            ("bmHeight", wintypes.LONG),
            ("bmWidthBytes", wintypes.LONG),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    _user32.GetIconInfo.argtypes = [HICON, ctypes.POINTER(ICONINFO)]
    _user32.GetIconInfo.restype = wintypes.BOOL
    _user32.GetDC.argtypes = [ctypes.c_void_p]
    _user32.GetDC.restype = ctypes.c_void_p
    _user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _gdi32.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  ctypes.c_void_p]
    _gdi32.GetObjectW.restype = ctypes.c_int
    _gdi32.GetDIBits.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
    ]
    _gdi32.GetDIBits.restype = ctypes.c_int
    _gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    _gdi32.DeleteObject.restype = wintypes.BOOL


# ---------------------------------------------------------------- 图标提取

_ICON_CACHE = {}          # path -> tuple(宽, 高, bytes) 或 None


def extract_icon_rgba(path: str, size: int = 16):
    """从 exe 抽图标，返回 (w, h, RGBA bytes)。失败返回 None。

    结果按路径缓存——同一个 exe 会被多个连接复用，反复抽取很浪费。
    """
    if not _IS_WIN or not path or not os.path.exists(path):
        return None
    key = (path, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    result = None
    large = ctypes.c_void_p()
    small = ctypes.c_void_p()
    try:
        n = _shell32.ExtractIconExW(path, 0, ctypes.byref(large),
                                    ctypes.byref(small), 1)
        hicon = small.value or large.value
        if n and hicon:
            result = _hicon_to_rgba(hicon)
    except Exception:  # noqa: BLE001
        result = None
    finally:
        # 抽到后必须释放，否则每次扫描都会泄漏 GDI 句柄
        for h in (large.value, small.value):
            if h:
                try:
                    _user32.DestroyIcon(ctypes.c_void_p(h))
                except Exception:  # noqa: BLE001
                    pass

    _ICON_CACHE[key] = result
    return result


def _hicon_to_rgba(hicon):
    """把 HICON 读成 RGBA 像素。

    图标位图是 32bpp BGRA，用负高度请求 DIB 可拿到顶朝下的行序，
    且自带 alpha 通道，透明背景能保留。
    """
    info = ICONINFO()
    if not _user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    hbm = info.hbmColor or info.hbmMask
    if not hbm:
        return None
    hdc = None
    try:
        bm = BITMAP()
        if not _gdi32.GetObjectW(hbm, ctypes.sizeof(BITMAP),
                                 ctypes.byref(bm)):
            return None
        w, h = bm.bmWidth, bm.bmHeight
        if w <= 0 or h <= 0 or w > 512 or h > 512:
            return None

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h          # 负值 = 顶朝下
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0      # BI_RGB

        buf_len = w * h * 4
        buf = ctypes.create_string_buffer(buf_len)
        hdc = _user32.GetDC(None)
        got = _gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bmi), 0)
        if not got:
            return None

        raw = buf.raw[:buf_len]
        out = bytearray(buf_len)
        for i in range(0, buf_len, 4):
            b, g, r, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            if a == 0 and (b or g or r):
                a = 255          # 老式无 alpha 图标：不透明部分补满
            out[i], out[i + 1], out[i + 2], out[i + 3] = r, g, b, a
        return (w, h, bytes(out))
    finally:
        if hdc:
            try:
                _user32.ReleaseDC(None, hdc)
            except Exception:  # noqa: BLE001
                pass
        for hb in (info.hbmColor, info.hbmMask):
            if hb:
                try:
                    _gdi32.DeleteObject(hb)
                except Exception:  # noqa: BLE001
                    pass


def icon_to_photo(rgba, size=None, master=None):
    """把 (w,h,RGBA) 转成 Tk PhotoImage。需要 Pillow。"""
    if not rgba:
        return None
    try:
        from PIL import Image, ImageTk
    except Exception:  # noqa: BLE001
        return None
    w, h, data = rgba
    try:
        im = Image.frombytes("RGBA", (w, h), data)
        if size and (w != size or h != size):
            im = im.resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(im, master=master)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- 文件定位


def _explorer_path() -> str:
    """explorer.exe 的绝对路径。

    不能用裸 "explorer"：本环境下 Popen 列表式调用会报
    FileNotFoundError [WinError 2]（PATH 里找不到），拿不到进程句柄。
    """
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(windir, "explorer.exe")


def reveal_in_explorer(path: str) -> bool:
    """在资源管理器里打开该文件所在的目录，并选中它。

    用 `explorer /select,"文件"` —— 唯一不需要额外权限的做法。
    explorer 对成功也常返回非 0，所以不看返回码，只看进程是否起来。
    """
    if not path or not os.path.exists(path):
        return False
    exe = _explorer_path()
    if not os.path.exists(exe):
        exe = "explorer"
    try:
        # 参数里的逗号是 /select 的语法，整段作为一个 argv 传，别拆开
        subprocess.Popen([exe, "/select," + path])
        return True
    except OSError:
        return False


def open_file(path: str) -> bool:
    """用系统默认程序打开该文件。"""
    if not path or not os.path.exists(path):
        return False
    try:
        os.startfile(path)  # noqa: S606  (Windows 专有)
        return True
    except (OSError, AttributeError):
        return False


def read_version_info(path: str) -> dict:
    """读 exe 的版本资源：公司名、产品名、文件说明。

    用于在没图标时给个更好的兜底显示，也补充「这是什么软件」的信息。
    """
    out = {}
    if not _IS_WIN or not path or not os.path.exists(path):
        return out
    try:
        import win32api  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        pass

    # 不依赖 pywin32：用 PowerShell 取一次（只在需要时调用，不参与批量路径获取）
    script = (
        "$p = Get-Item -LiteralPath "
        + _ps_quote(path)
        + " -ErrorAction SilentlyContinue;"
        " if ($p) { $v = $p.VersionInfo;"
        " \"$($v.CompanyName)`t$($v.ProductName)`t$($v.FileDescription)\" }"
    )
    try:
        from connscan import _run_text, _PS
        text, _ = _run_text(_PS + [script], timeout=20)
    except Exception:  # noqa: BLE001
        return out
    parts = (text.strip().splitlines() or [""])[0].split("\t")
    for k, v in zip(("company", "product", "desc"), parts):
        if v.strip():
            out[k] = v.strip()
    return out


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
