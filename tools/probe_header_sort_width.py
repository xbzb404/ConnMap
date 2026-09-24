# -*- coding: utf-8 -*-
"""量表头文字与列宽，确认加「↑/↓」排序标记后不会撞到下一列。

列宽与列定义都从**运行期的 App 实例**读，不写死常量 ——
`self.widths` 会随尾列数据量宽变化，写死的副本必然漂移。

用法：python tools/probe_header_sort_width.py
"""
import ctypes
import os
import sys
import tkinter as tk
from tkinter import font as tkfont

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:                                     # noqa: BLE001
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:                                 # noqa: BLE001
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app as A  # noqa: E402

root = tk.Tk()
print(f"屏幕 DPI: {root.winfo_fpixels('1i'):.1f}   (96=100%, 144=150%)")

inst = A.App(root)
root.update()

tiny = tkfont.Font(family=inst.family, size=8)     # fonts["tiny"] 的等价体
PAD = 10
arrow_w = tiny.measure(" ↑")

print(f"中文族: {inst.family}    tiny(8pt) 箭头宽 = {arrow_w}")
print()
print(f"{'列标题':<24}{'列宽':>6}{'标题宽':>8}{'+箭头':>8}   结论")
print("-" * 72)
x = PAD
worst = []
for text, key in A.App.COLS:
    w = inst.widths[key]
    tw = tiny.measure(text)
    tot = tw + arrow_w
    fits = tot <= w - 6
    mark = "OK 有位置画箭头" if fits else f"⚠ 差 {tot - w + 6}px → 只靠颜色标识"
    print(f"{text:<24}{w:>6}{tw:>8}{tot:>8}   {mark}")
    if not fits:
        worst.append((text, key, tot - w + 6))
    x += w

print(f"\n总表宽 = {x + 10}")
if worst:
    print("\n以下列放不下箭头（设计上允许，_draw_header 会自动省略）：")
    for text, key, over in worst:
        print(f"   {text}（{key}）差 {over}px")

# 顺带确认：箭头画在「放得下」的列上时，右边界确实没越界
print("\n逐列边界校验（标题 + 箭头是否越过该列右边界）：")
x = PAD
for text, key in A.App.COLS:
    w = inst.widths[key]
    need = tiny.measure(text) + arrow_w
    ok = x + need <= x + w - 6
    print(f"  {text:<24} x={x:>5} 右边界={x + w:>5} 需要到={x + need:>5}  "
          f"{'✓' if ok else '✗'}")
    x += w

root.destroy()
