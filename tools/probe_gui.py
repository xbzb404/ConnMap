# -*- coding: utf-8 -*-
"""GUI 自报状态探针：构建窗口、跑一轮扫描 + 定位，把结果落盘。

沙箱会话看不到子进程创建的窗口，所以让程序自己汇报状态。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import tkinter as tk  # noqa: E402

import app as gui  # noqa: E402

REPORT = os.path.join(HERE, "_probe_result.json")
SHOT = os.path.join(ROOT, "_shots", "gui_probe.png")


def shot(root, path):
    """截图。窗口必须在最前，否则截到的是压在它上面的程序。"""
    from PIL import ImageGrab
    root.update()
    time.sleep(0.35)
    for _ in range(4):
        root.lift()
        root.attributes("-topmost", True)
        root.update()
        time.sleep(0.35)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    root.attributes("-topmost", False)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)
    return img.size


def main():
    info = {}
    root = tk.Tk()
    obj = gui.App(root)
    root.geometry("1280x840+40+30")
    root.update()

    info["winfo_viewable"] = root.winfo_viewable()
    info["winfo_ismapped"] = root.winfo_ismapped()
    info["geometry"] = root.winfo_geometry()
    info["title"] = root.title()

    # 跑完扫描
    deadline = time.time() + 180
    while time.time() < deadline:
        root.update()
        obj.drain_events()
        if obj.worker is None or not obj.worker.is_alive():
            break
        time.sleep(0.05)
    for _ in range(10):
        root.update()
        obj.drain_events()
        time.sleep(0.08)

    info["scan_conns"] = len(obj.conns)
    info["scan_cost"] = round(obj.scan_cost, 2)
    info["filtered"] = len(obj.filtered)
    info["rows"] = len(obj.rows)
    info["error"] = obj.last_error
    info["stats"] = obj.stats_lbl.cget("text")
    info["status"] = obj.status_lbl.cget("text")

    # 跑地理位置查询
    obj.run_geo()
    deadline = time.time() + 300
    while time.time() < deadline:
        root.update()
        obj.drain_events()
        if obj.worker is None or not obj.worker.is_alive():
            break
        time.sleep(0.05)
    for _ in range(12):
        root.update()
        obj.drain_events()
        time.sleep(0.1)

    located = [c for c in obj.conns if (c.geo or {}).get("ok")]
    info["located"] = len(located)
    info["geo_status"] = obj.status_lbl.cget("text")
    info["geo_label"] = obj.geo_lbl.cget("text")
    info["summary_rows"] = len(obj.sum_inner.winfo_children())

    # 抽样记录几条定位结果
    samples = []
    seen = set()
    for c in obj.conns:
        g = c.geo or {}
        if g.get("ok") and c.remote_ip not in seen:
            seen.add(c.remote_ip)
            samples.append({
                "proc": c.proc_name,
                "ip": c.remote_ip,
                "region": gui.geoloc.describe_region(g),
                "network": gui.geoloc.short_network(g),
                "cloud": bool(g.get("is_cloud")),
                "cdn": bool(g.get("is_cdn")),
                "source": g.get("source"),
            })
    info["samples"] = samples[:14]

    # 切到「按应用」视图 + 搜索，确认筛选链路可用
    obj.set_group("app")
    root.update()
    info["app_mode_rows"] = len(obj.sum_inner.winfo_children())
    obj.set_group("region")

    obj.search_var.set(samples[0]["ip"] if samples else "")
    root.update()
    info["search_filtered"] = len(obj.filtered)
    obj.search_var.set("")
    root.update()

    rep = obj.build_report()
    info["report_chars"] = len(rep)
    with open(os.path.join(HERE, "_report_sample.txt"), "w",
              encoding="utf-8") as f:
        f.write(rep)

    root.update()
    time.sleep(0.4)
    try:
        size = shot(root, SHOT)
        info["screenshot"] = SHOT
        info["screenshot_size"] = size
    except Exception as exc:  # noqa: BLE001
        info["screenshot_error"] = f"{type(exc).__name__}: {exc}"

    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    print("PROBE_OK conns=%s located=%s rows=%s" %
          (info["scan_conns"], info["located"], info["rows"]))
    root.destroy()


if __name__ == "__main__":
    main()
