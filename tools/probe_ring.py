# -*- coding: utf-8 -*-
"""截图验证：概览卡进度环排版（DPI 感知版）。

本机是 1.5x 缩放：Tk 报逻辑像素、ImageGrab 吃物理像素。
必须先 SetProcessDpiAwareness 让两边口径一致，否则截出来的
区域会整体偏掉 1.5 倍。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk

from PIL import ImageGrab

import app as A

SHOT_DIR = os.path.join(ROOT, "_shots")
os.makedirs(SHOT_DIR, exist_ok=True)

root = tk.Tk()
sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
w, h = min(1360, sw - 80), min(870, sh - 140)
root.geometry(f"{w}x{h}+20+20")


def compose():
    for _ in range(5):
        try:
            root.deiconify()
            root.state("normal")
            root.lift()
            root.attributes("-topmost", True)
            root.update()
        except tk.TclError:
            pass
    root.attributes("-topmost", False)
    root.update()


obj = A.App(root)
compose()
print(f"屏幕 {sw}x{sh}  DPI {root.winfo_fpixels('1i'):.0f}")

# 环的几种状态都看一眼
cases = [(1.0, "定位"), (0.57, "定位"), (0.0, "定位")]
for i, (val, sub) in enumerate(cases):
    obj.ring.set_value(val, sub=sub)
    compose()
    for _ in range(8):
        root.update()
        time.sleep(0.04)
    compose()
    cx, cy = obj.card_overview.winfo_rootx(), obj.card_overview.winfo_rooty()
    cw, ch = obj.card_overview.winfo_width(), obj.card_overview.winfo_height()
    im = ImageGrab.grab((cx - 3, cy - 3, cx + cw + 3, cy + ch + 3))
    p = os.path.join(SHOT_DIR, f"overview_{int(val*100)}.png")
    im.save(p)
    print(f"  {p}  环={obj.ring.winfo_width()}x{obj.ring.winfo_height()}  "
          f"卡={cw}x{ch}")

# 整窗一张
compose()
rx, ry = root.winfo_rootx(), root.winfo_rooty()
im = ImageGrab.grab((rx, ry, rx + root.winfo_width(), ry + root.winfo_height()))
p = os.path.join(SHOT_DIR, "gui_ring_fixed.png")
im.save(p)
print(f"整窗 {p} {im.size}")

root.destroy()
print("done")
