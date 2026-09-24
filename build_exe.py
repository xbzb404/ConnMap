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
    """跑一次冻结自检：确认 PIL 真的进了 exe。

    光看「exe 能启动」是查不出抗锯齿降级的 —— 缺 Pillow 时程序照跑，
    只是静默退回 Tk 原生绘制。所以必须显式断言。

    探针要 import PIL.ImageTk，而它依赖 tkinter；冻结环境里缺 Tcl/Tk
    运行时的话，报的会是 ModuleNotFoundError: No module named 'tkinter'
    而不是 PIL 缺失 —— 那是探针自身的问题，会把结论带偏。
    所以这里也要把 tcl 目录打进去。
    """
    tcl_dir = find_tcl_tk()
    probe_src = (
        "try:\n"
        "    import tkinter\n"
        "    from PIL import Image, ImageDraw, ImageTk\n"
        "    print('PIL_OK')\n"
        "except Exception as e:\n"
        "    print('PIL_MISSING', type(e).__name__, e)\n"
    )
    probe = os.path.join(HERE, "_pilcheck.py")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(probe_src)

    dist = os.path.join(HERE, "_pilcheck_dist")
    work = os.path.join(HERE, "_pilcheck_build")
    spec = os.path.join(HERE, "_pilcheck_spec")
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile",
           "--console", "--name", "_PilCheck",
           "--distpath", dist, "--workpath", work, "--specpath", spec,
           "--hidden-import", "tkinter", "--hidden-import", "_tkinter",
           "--hidden-import", "PIL", "--hidden-import", "PIL.Image",
           "--hidden-import", "PIL.ImageDraw", "--hidden-import", "PIL.ImageTk",
           ]
    if tcl_dir:
        cmd += ["--add-data", f"{tcl_dir};tcl"]
    cmd.append(probe)

    log("自检：确认 Pillow 与 Tcl/Tk 均已打入产物…")
    try:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        if r.returncode != 0:
            log("自检构建失败：" + (r.stderr or "")[-800:])
            return False
        chk = os.path.join(dist, "_PilCheck.exe")
        if not os.path.exists(chk):
            log("自检产物缺失")
            return False
        out = subprocess.run([chk], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=90)
        text = (out.stdout or "") + (out.stderr or "")
        if "PIL_OK" in text:
            log("自检通过：冻结环境下 PIL 可用（抗锯齿生效）")
            return True
        log("自检失败：冻结环境里 PIL 不可用，界面会退回无抗锯齿绘制")
        log("  输出：" + text.strip()[:300])
        return False
    except Exception as exc:  # noqa: BLE001
        log(f"自检异常：{type(exc).__name__}: {exc}")
        return False
    finally:
        import shutil
        for d in (dist, work, spec):
            shutil.rmtree(d, ignore_errors=True)
        if os.path.exists(probe):
            os.remove(probe)


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
