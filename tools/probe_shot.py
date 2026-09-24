"""抓一张默认窗口的成品截图，肉眼复核。

要点：
  · 必须开 DPI 感知，否则 winfo_* 报逻辑像素、ImageGrab 吃物理像素，
    两者混用会抓偏（1.5x 缩放）。
  · 窗口要抬到前台再抓，否则会抓到压在它上面的程序。
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

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

from PIL import ImageGrab  # noqa: E402

import app as A  # noqa: E402

SHOT_DIR = os.path.join(ROOT, "_shots")
os.makedirs(SHOT_DIR, exist_ok=True)


def main():
    root = tk.Tk()
    inst = A.App(root)

    t0 = time.time()
    while time.time() - t0 < 14:
        root.update()
        if inst.rows and inst.geo_worker is None and len(inst.rows) > 5:
            break
        time.sleep(0.05)

    # 让地理位置查一会儿，截图上才有国家/城市
    t0 = time.time()
    while time.time() - t0 < 18:
        root.update()
        time.sleep(0.05)

    root.deiconify()
    root.state("normal")
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(8):
        root.update()
    time.sleep(0.6)
    root.update()

    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()
    print(f"窗口 {w}x{h} @ {x},{y}   行数 {len(inst.rows)}  表宽 {inst.table_w}")
    print(f"canvas 宽 {inst.canvas.winfo_width()}  需横向滚 "
          f"{inst.table_w > inst.canvas.winfo_width()}")

    fp = os.path.join(SHOT_DIR, "final_default.png")
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(fp)
    print("saved", fp)

    # 再单独抓表格左上区域放大看列头与 IP 列
    fp2 = os.path.join(SHOT_DIR, "final_table.png")
    ImageGrab.grab(bbox=(x, y + 120, x + w, y + h - 40)).save(fp2)
    print("saved", fp2)

    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
