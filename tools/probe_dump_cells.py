"""把界面上每个单元格实际渲染的文字导出来，用于排查显示异常（乱码/截断）。

不做断言，只落盘 _shots/cells_dump.txt，供人工与脚本复核。
"""
import ctypes
import os
import sys
import time
import tkinter as tk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app as A          # noqa: E402
import geoloc            # noqa: E402

OUT = os.path.join(ROOT, "_shots", "cells_dump.txt")
os.makedirs(os.path.dirname(OUT), exist_ok=True)


def cell_texts(row):
    out = []
    for w in row.cells:
        try:
            out.append(w.cget("text"))
        except tk.TclError:
            out.append("")
    return out


def main():
    root = tk.Tk()
    inst = A.App(root)

    t0 = time.time()
    while time.time() - t0 < 60:
        root.update()
        done = inst.geo_progress[1] or 0
        if inst.rows and inst.geo_worker is None and done >= 1:
            break
        time.sleep(0.05)
    root.update()

    lines = []
    lines.append(f"rows={len(inst.rows)} conns={len(inst.conns)} "
                 f"filtered={len(inst.filtered)} geo={inst.geo_progress}")
    lines.append("")
    lines.append("=== 单元格实际文字"
                 "（进程|状态|IP|端口|PID|省|市|区|经纬度|运营商） ===")
    for r in inst.rows:
        lines.append(" | ".join(cell_texts(r)))
    lines.append("")
    lines.append("=== 每行 conn.geo 原始字段 ===")
    for r in inst.rows:
        g = r.conn.geo or {}
        lines.append(repr({k: g.get(k) for k in
                           ("ip", "country", "province", "city", "district",
                            "datacenter", "isp", "org", "lat", "lon",
                            "source", "ok")}))
    lines.append("")
    lines.append("=== 报告全文 ===")
    lines.append(inst.build_report())

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("written:", OUT)

    inst.stop_flag = True
    try:
        root.destroy()
    except tk.TclError:
        pass


if __name__ == "__main__":
    main()
