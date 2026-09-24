"""验证打包后的 exe 是否真的能显示界面并完成扫描。

重要经验：不能用 EnumWindows 从外部枚举窗口。
本环境的沙箱会话与交互桌面不同，跨会话看不到彼此的窗口，
会导致「打包成功但检测不到窗口」的假失败。

正确做法：让被测程序**自己上报**状态——
  winfo_viewable / winfo_ismapped / 窗口尺寸 / 扫描结果 / 自截图，
写到临时目录的日志里，再由本脚本读取判定。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
EXE = os.path.join(ROOT, "dist", "ConnMap.exe")
SHOTS = os.path.join(ROOT, "_shots")
PROBE = os.path.join(HERE, "probe.py")
PROBE_LOG = os.path.join(tempfile.gettempdir(), "connmap_probe.log")
PROBE_PNG = os.path.join(tempfile.gettempdir(), "connmap_probe.png")


def build_probe() -> str:
    """生成探针：导入真实 app 模块，建界面、扫描、定位、自截图。"""
    src = '''
import os, sys, tempfile, time, traceback

LOG = os.path.join(tempfile.gettempdir(), "connmap_probe.log")
PNG = os.path.join(tempfile.gettempdir(), "connmap_probe.png")


def w(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(msg) + "\\n")


def vis(root, tag):
    """记录窗口可见性。

    注意：必须在**长任务开始前**先测一次。
    本探针用 root.update() 手动驱动事件循环、不进 mainloop，
    窗口若一直没被抬到前台合成（Windows 上被别的窗口压住、
    或从未 SetForegroundWindow），winfo_ismapped/viewable 会是 0，
    截图也只会得到全黑——这是探针环境的假象，不是程序的问题。
    """
    root.update()
    w("%-22s viewable=%s ismapped=%s state=%s" % (
        tag, root.winfo_viewable(), root.winfo_ismapped(), root.state()))


def compose(root):
    """把窗口真正抬到前台，让 Windows 合成它，之后截图才有内容。"""
    try:
        root.deiconify()
        root.state("normal")
    except tk.TclError:
        pass
    for _ in range(4):
        try:
            root.lift()
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        root.update()
        time.sleep(0.3)


def drain(obj, root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        obj.drain_events()
        if obj.worker is None or not obj.worker.is_alive():
            break
        time.sleep(0.05)
    for _ in range(8):
        root.update(); obj.drain_events(); time.sleep(0.08)


try:
    import tkinter as tk
    w("tkinter ok")

    import app as A
    w("app module imported from %s" % A.__file__)

    root = tk.Tk()
    inst = A.App(root)
    root.geometry("1280x840+40+30")
    compose(root)
    w("App built")
    vis(root, "T0 after App()")

    # 扫描
    drain(inst, root, 120)
    w("scan_conns=%d cost=%.2f" % (len(inst.conns), inst.scan_cost))
    vis(root, "T1 scan done")

    # 地理位置
    inst.run_geo()
    drain(inst, root, 240)
    vis(root, "T2 geo done")

    located = [c for c in inst.conns if (c.geo or {}).get("ok")]
    w("located=%d rows=%d filtered=%d" % (len(located), len(inst.rows), len(inst.filtered)))
    w("stats=%s" % inst.stats_lbl.cget("text"))
    w("status=%s" % inst.status_lbl.cget("text"))
    w("error=%s" % inst.last_error)

    # 采样几条真实结果
    seen = set()
    for c in inst.conns:
        g = c.geo or {}
        if g.get("ok") and c.remote_ip not in seen:
            seen.add(c.remote_ip)
            tag = "CDN" if g.get("is_cdn") else ("云机房" if g.get("is_cloud") else "-")
            w("  sample %-18s %-20s %-30s %s" % (
                c.remote_ip, A.geoloc.describe_region(g),
                A.geoloc.short_network(g)[:30], tag))

    w("winfo_viewable=%s" % root.winfo_viewable())
    w("winfo_ismapped=%s" % root.winfo_ismapped())
    w("geometry=%s" % root.winfo_geometry())
    w("title=%s" % root.title())

    rep = inst.build_report()
    w("report_chars=%d" % len(rep))

    # 截图前再抬一次前台，否则截到的是压在它上面的程序（或全黑）
    compose(root)
    try:
        from PIL import ImageGrab
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ww, hh = root.winfo_width(), root.winfo_height()
        vis(root, "T3 before grab")
        ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(PNG)
        root.attributes("-topmost", False)
        w("screenshot ok %sx%s" % (ww, hh))
    except Exception as exc:
        w("screenshot failed: %s" % exc)

    # 最终判定用的可见性（此时窗口已在前台）
    vis(root, "T4 final")

    root.destroy()
    w("done")
except Exception:
    w("EXCEPTION:\\n" + traceback.format_exc())
'''
    with open(PROBE, "w", encoding="utf-8") as f:
        f.write(src)
    return PROBE


def main() -> int:
    if not os.path.exists(EXE):
        print("未找到 exe：", EXE)
        return 1

    size_mb = os.path.getsize(EXE) / 1024 / 1024
    print(f"exe 体积：{size_mb:.1f} MB")

    probe = build_probe()
    print("构建探针程序（内嵌真实 app 模块）…")

    sys.path.insert(0, ROOT)
    import build_exe as B

    tcl = B.find_tcl_tk()
    env = dict(os.environ)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"

    outdir = "_probedist"
    for d in (outdir, "_probebuild", "_probespec"):
        p = os.path.join(ROOT, d)
        if os.path.exists(p):
            os.rename(p, p + "_old_" + time.strftime("%H%M%S"))

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed", "--name", "ConnMapProbe",
        "--distpath", outdir, "--workpath", "_probebuild",
        "--specpath", "_probespec",
        "--add-data", f"{tcl};tcl",
        "--add-data", f"{os.path.join(ROOT, 'icon.ico')};.",
        "--add-data", f"{os.path.join(ROOT, 'icon.png')};.",
        "--hidden-import", "tkinter", "--hidden-import", "_tkinter",
        "--hidden-import", "tkinter.font", "--hidden-import", "tkinter.ttk",
        probe,
    ]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        print("探针构建失败：")
        print((r.stdout or "")[-2000:])
        print((r.stderr or "")[-2000:])
        return 1
    print("探针构建完成，开始运行…")

    for p in (PROBE_LOG, PROBE_PNG):
        if os.path.exists(p):
            os.remove(p)

    exe = os.path.join(ROOT, outdir, "ConnMapProbe.exe")
    proc = subprocess.Popen([exe])
    try:
        proc.wait(timeout=420)
    except subprocess.TimeoutExpired:
        proc.terminate()
        print("探针超时未退出")
        return 1

    print("探针返回码：", proc.returncode)
    print("-" * 60)
    if not os.path.exists(PROBE_LOG):
        print("未生成探针日志——程序可能未启动")
        return 1

    log = open(PROBE_LOG, encoding="utf-8", errors="replace").read()
    print(log)

    os.makedirs(SHOTS, exist_ok=True)
    black = False
    if os.path.exists(PROBE_PNG):
        import shutil
        shutil.copy(PROBE_PNG, os.path.join(SHOTS, "exe_ui.png"))
        try:
            from PIL import Image
            im = Image.open(PROBE_PNG).convert("RGB")
            cols = sorted(im.getcolors(maxcolors=1 << 22) or [], reverse=True)
            distinct = sum(n for n, _ in cols)
            if distinct <= 1:
                black = True
                print("界面截图全黑！")
        except Exception as exc:  # noqa: BLE001
            print("截图检查失败：", exc)
        print("界面截图已保存：_shots/exe_ui.png")

    # 结论只看 T4（窗口已在前台、刚截完图）这一行的可见性
    final_line = [ln for ln in log.splitlines() if ln.startswith("T4 final")]
    final_vis = "viewable=1" in (final_line[-1] if final_line else "")
    ok = (final_vis and "screenshot ok" in log and not black
          and "report_chars=" in log and "EXCEPTION" not in log
          and "scan_conns=0" not in log)
    if not final_vis:
        print("可见性判定行：" + (final_line[-1] if final_line else "(缺失)"))
    print("-" * 60)
    print("结论：" + ("打包后的 exe 界面与扫描、定位功能均正常"
                    if ok else "存在异常，请查看上方日志"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
