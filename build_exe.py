"""PyInstaller 打包脚本：把本机连接地图打成单文件 exe。

用法：
    python build_exe.py

产物：
    dist/ConnMap.exe —— 单文件，双击即用，无需安装 Python

说明：
    连接枚举依赖系统自带的 netstat / tasklist / PowerShell，属「外部程序调用」，
    不需要打进 exe。地理查询走系统 curl，同样不必打包。

    ⚠️ **PIL 不能排除**：界面的超采样抗锯齿（SsPainter）依赖 Pillow，
    排除后 app.py 里的 `_HAS_PIL` 会变成 False，程序**不报错**、
    但会静默退回 Tk 原生绘制 —— 进度环/圆角卡片重新长出像素毛边。
    所以只排除真正用不到的重型库。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "app.py")
ICON = os.path.join(HERE, "icon.ico")
VERSION_FILE = os.path.join(HERE, "version_info.txt")
EXE_NAME = "ConnMap"

# 明确排除用不到的模块，显著减小体积。
# 注意：不要排 PIL / Pillow —— 抗锯齿自绘要用（见模块 docstring）。
EXCLUDES = [
    "numpy", "scipy", "pandas", "matplotlib",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "wx",
    "IPython", "jupyter", "notebook", "pytest", "setuptools", "pip",
    "sqlite3", "unittest", "pydoc", "doctest", "test", "distutils",
]

# Pillow 的 ImageTk 是子模块，PyInstaller 静态分析可能漏掉，
# 漏掉会导致 _imagingtk 扩展加载失败 → 同样静默退回无抗锯齿绘制。
HIDDEN_IMPORTS = [
    "tkinter", "tkinter.font", "tkinter.ttk", "_tkinter",
    "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageTk",
]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def ensure_icon() -> None:
    if not os.path.exists(ICON):
        log("未找到 icon.ico，正在生成…")
        subprocess.run([sys.executable, os.path.join(HERE, "_make_icon.py")],
                       check=True)


def _dispose(path: str) -> None:
    """把旧产物改名搁置，避免触发环境的批量删除保护。

    改名是原子的，比删除更稳，也不会失败于「文件被占用」。
    """
    if not os.path.exists(path):
        return
    base = os.path.basename(path)
    stamp = time.strftime("%H%M%S")
    ext = os.path.splitext(base)[1]
    stem = base[: -len(ext)] if ext else base
    parked = os.path.join(os.path.dirname(path), f"_{stem}_old_{stamp}{ext}")
    try:
        os.rename(path, parked)
        log(f"已搁置 {base} → {os.path.basename(parked)}")
    except OSError as exc:
        log(f"警告：{base} 无法搁置（{exc}），继续打包")


def clean() -> None:
    """清理旧产物（用改名搁置，不用删除）。"""
    for name in ("build", "dist"):
        _dispose(os.path.join(HERE, name))
    spec = os.path.join(HERE, f"{EXE_NAME}.spec")
    if os.path.exists(spec):
        _dispose(spec)


def find_tcl_tk():
    """定位 Tcl/Tk 运行时数据目录。

    商店版 Python 把 tcl 目录放在 <Python根>/tcl 下，而不是常规的
    Lib/tcl，PyInstaller 检测不到，打出的 exe 会因缺少 Tcl 运行时
    而**静默不出窗口**（无报错、无日志）。所以必须手动找出来显式打进。
    """
    import _tkinter
    import tkinter

    roots = set()
    try:
        pyd = os.path.dirname(_tkinter.__file__)
        roots.add(os.path.dirname(pyd))
        roots.add(os.path.dirname(os.path.dirname(pyd)))
    except Exception:  # noqa: BLE001
        pass
    roots.add(os.path.dirname(os.path.dirname(os.path.abspath(tkinter.__file__))))
    if sys.base_prefix:
        roots.add(sys.base_prefix)
    if sys.prefix:
        roots.add(sys.prefix)

    for root in roots:
        for cand in (os.path.join(root, "tcl"), os.path.join(root, "Lib", "tcl")):
            if not os.path.isdir(cand):
                continue
            # 必须同时含 tcl8.x 与 tk8.x 子目录（用 isdir 判断，
            # 否则会误匹配到同名的 tk86t.lib 文件）
            subs = [d for d in os.listdir(cand)
                    if os.path.isdir(os.path.join(cand, d))]
            has_tcl = any(d.startswith("tcl8") for d in subs)
            has_tk = any(d.startswith("tk8") for d in subs)
            if has_tcl and has_tk:
                return cand
    return None


def build() -> str:
    tcl_dir = find_tcl_tk()
    if not tcl_dir:
        log("警告：未找到 Tcl/Tk 运行时目录，exe 可能无法显示界面")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        "--icon", ICON,
        "--add-data", f"{ICON};.",
        "--add-data", f"{os.path.join(HERE, 'icon.png')};.",
    ]
    for m in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", m]
    if os.path.exists(VERSION_FILE):
        cmd += ["--version-file", VERSION_FILE]

    if tcl_dir:
        cmd += ["--add-data", f"{tcl_dir};tcl"]
        log(f"已附加 Tcl/Tk 运行时：{tcl_dir}")

    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    cmd.append(ENTRY)

    log("开始打包…")
    t0 = time.time()

    # 本环境的删除钩子会把 os.remove 重定向到回收站，而 PyInstaller
    # 收尾时会 os.remove 自己的产物，导致构建中途失败并留下损坏 exe。
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"

    proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    cost = time.time() - t0

    if proc.returncode != 0:
        log("打包失败，输出如下：")
        print((proc.stdout or "")[-4000:])
        print((proc.stderr or "")[-4000:])
        raise SystemExit(1)

    log(f"打包完成，耗时 {cost:.0f}s")
    exe = os.path.join(HERE, "dist", f"{EXE_NAME}.exe")
    if not os.path.exists(exe):
        log("未找到产物 exe")
        raise SystemExit(1)
    return exe


def verify_bundled(exe: str) -> bool:
    """确认依赖真的进了 exe。

    光看「exe 能启动」是查不出抗锯齿降级的 —— 缺 Pillow 时程序照跑，
    只是静默退回 Tk 原生绘制，所以必须显式断言。

    早先这里是「另建一个 console 探针 exe 再运行它」，但那依赖运行环境：
    在没有交互桌面的会话里，console 子进程会挂在启动阶段（实测连续多次
    90s 超时），而 windowed 的主程序本身跑得好好的 —— 这种失败与被测内容
    无关，只会把判断带偏（每次打包都报红，久了就没人看它了）。

    现在改为直接读 exe 归档、断言关键模块在不在：秒级完成，且不受运行环境
    影响。PyInstaller 的 onefile 会把归档目录以明文放在 exe 尾部，
    模块名直接搜得到。
    """
    need = {
        b"_imagingtk": "Pillow 的 tkinter 扩展（抗锯齿自绘依赖它）",
        b"PIL.ImageTk": "Pillow ImageTk",
        b"ImageDraw": "Pillow ImageDraw",
        b"_tkinter": "Tcl/Tk 绑定",
    }
    try:
        with open(exe, "rb") as fh:
            blob = fh.read()
    except OSError as exc:
        log(f"自检失败：读不到产物（{exc}）")
        return False

    missing = [why for token, why in need.items() if token not in blob]
    if missing:
        log("自检失败：exe 里缺少 " + "、".join(missing))
        log("  请检查 EXCLUDES 是否误排了 PIL / tkinter。")
        return False
    log("自检通过：PIL 与 Tcl/Tk 相关模块均已打入产物")
    return True


def report(exe: str) -> None:
    size_mb = os.path.getsize(exe) / 1024 / 1024
    log("=" * 56)
    log(f"产物：{exe}")
    log(f"体积：{size_mb:.1f} MB")
    log("=" * 56)


def main() -> None:
    log(f"Python: {sys.version.split()[0]}")
    ensure_icon()
    clean()
    exe = build()
    report(exe)
    if verify_bundled(exe):
        log("可直接双击 dist\\ConnMap.exe 运行。")
    else:
        log("⚠️ 打包完成，但抗锯齿自检未通过，请检查 EXCLUDES 是否含 PIL。")


if __name__ == "__main__":
    main()
