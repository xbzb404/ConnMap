"""确认横向滚动后，最右侧的「运营商/机房」列完整可见（含 [CDN]/[云机房] 标签）。"""

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
        if inst.rows and len(inst.rows) > 5:
            break
        time.sleep(0.05)
    t0 = time.time()
    while time.time() - t0 < 14:
        root.update()
        time.sleep(0.05)

    root.deiconify()
    root.state("normal")
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(8):
        root.update()
    time.sleep(0.5)

    print(f"表宽 {inst.table_w}  可视 {inst.canvas.winfo_width()}")
    # 滚到最右
    inst.canvas.xview_moveto(1.0)
    for _ in range(6):
        root.update()
    time.sleep(0.4)
    root.update()
    x = inst.canvas.xview()
    print(f"xview 位置 {x}（1.0 = 最右）")

    # 打印最右列在这些行里的实际文本
    print("「运营商/机房」列文本：")
    for r in inst.rows[:10]:
        print("   ", repr(r.cells[7].cget("text")))

    wx = root.winfo_rootx()
    wy = root.winfo_rooty()
    ww = root.winfo_width()
    wh = root.winfo_height()
    fp = os.path.join(SHOT_DIR, "final_scrolled.png")
    ImageGrab.grab(bbox=(wx, wy + 170, wx + ww, wy + wh - 30)).save(fp)
    print("saved", fp)

    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
