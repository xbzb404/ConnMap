# -*- coding: utf-8 -*-
"""验证「边查边显示」与抗锯齿渲染。

关注两件事：
  1. 地理结果是否**逐个**到达界面（而不是全查完才一次性铺出来）。
     判据：在 geo 线程还活着的时候，就已经有行拿到了 geo 并刷进了表格；
     并且「已定位行数」随时间单调上升，中途能采到多个中间值。
  2. 自绘控件的绘制图（_photo）是否非空 —— 说明走的是抗锯齿出图路径。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import tkinter as tk  # noqa: E402

import app as gui  # noqa: E402

LOG = os.path.join(HERE, "_live_probe.log")


def w(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")


def pump(obj, root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        obj.drain_events()
        time.sleep(0.03)


def located_rows(obj):
    """表格里已经有地理结果的连接行数。"""
    return sum(1 for r in obj.rows if (r.conn.geo or {}).get("ok"))


try:
    root = tk.Tk()
    obj = gui.App(root)
    root.geometry("1280x840+40+30")
    root.update()

    # 等扫描完成（扫描线程结束）
    end = time.time() + 120
    while time.time() < end:
        root.update()
        obj.drain_events()
        if obj.worker is None or not obj.worker.is_alive():
            break
        time.sleep(0.04)
    pump(obj, root, 0.6)
    w("scan_conns=%d rows=%d" % (len(obj.conns), len(obj.rows)))

    # 抗锯齿出图检查
    w("aa: PIL=%s ring_photo=%s" % (gui._HAS_PIL, obj.ring._photo is not None))

    # 关键观察：geo 跑的过程中采样「已定位行数」
    samples = []
    t0 = time.time()
    end = t0 + 300
    last = -1
    while time.time() < end:
        root.update()
        obj.drain_events()
        n = located_rows(obj)
        if n != last:
            samples.append((round(time.time() - t0, 2), n))
            last = n
        alive = obj.geo_worker is not None and obj.geo_worker.is_alive()
        if not alive and n > 0:
            # 线程结束且已出结果，再多收几轮事件
            for _ in range(15):
                root.update()
                obj.drain_events()
                time.sleep(0.05)
            break
        time.sleep(0.03)

    w("located 增量采样（时间秒, 已定位行数）:")
    for t, n in samples[:20]:
        w("   %6.2fs  %d" % (t, n))

    total_ips = len({c.remote_ip for c in obj.conns})
    located = sum(1 for c in obj.conns if (c.geo or {}).get("ok"))
    w("public_ips=%d located_conns=%d" % (total_ips, located))
    w("geo_label=%s" % obj.geo_lbl.cget("text"))
    w("status=%s" % obj.status_lbl.cget("text"))

    # 汇总面板是否随结果长出来
    w("summary_rows=%d" % len(obj.sum_inner.winfo_children()))

    # 结论：中途中出现过多个不同的定位行数 => 是流式的
    mid_values = sorted({n for _, n in samples if 0 < n < located})
    w("mid_values=%s" % mid_values)
    streaming = len(mid_values) >= 2
    w("结论: " + ("边查边显示生效（中途采到多个中间值）"
                  if streaming else "疑似一次性铺出，未采到中间值"))

    # 截图留证
    try:
        from PIL import ImageGrab
        root.lift()
        root.attributes("-topmost", True)
        for _ in range(4):
            root.update()
            time.sleep(0.3)
        x, y = root.winfo_rootx(), root.winfo_rooty()
        ww, hh = root.winfo_width(), root.winfo_height()
        os.makedirs(os.path.join(ROOT, "_shots"), exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(
            os.path.join(ROOT, "_shots", "gui_aa.png"))
        w("grab ok %sx%s" % (ww, hh))
    except Exception as exc:  # noqa: BLE001
        w("grab failed: %s" % exc)

    root.destroy()
    w("done")
except Exception:
    import traceback
    w("EXCEPTION:\n" + traceback.format_exc())
