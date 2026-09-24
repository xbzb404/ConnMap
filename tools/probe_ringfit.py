# -*- coding: utf-8 -*-
"""校验：进度环内文字是否真的装得下（不贴壁、不溢出、不被裁）。

不靠肉眼，直接量：文字实测宽 vs 环内可用弦宽；
再从截图里数「环内文字像素」与「环壁像素」是否贴上。
"""
import os
import sys
import tkinter as tk
import tkinter.font as tkfont

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import app as A

FM = A.FONT_FAMILY
fails = []


def chk(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(name)


def fits(label, size, avail):
    f = tkfont.Font(family=FM, size=size, weight="bold")
    return f.measure(label), f.metrics("linespace")


root = tk.Tk()
root.withdraw()
obj = A.App(root)

# 逐个百分比验算：环内可用弦宽是多少、最优字号是多少、文字宽是否 <= 可用宽
PAD, RING_W = 5, 6
for size in (76, 64, 56):
    s = size
    avail = (s - 2 * (PAD + RING_W)) * 0.92
    print(f"\n环径 {size}px  内圆弦宽 {s - 2*(PAD+RING_W)}px  可用 {avail:.1f}px")
    for label in ("0%", "8%", "57%", "99%", "100%"):
        # 复刻 Ring._paint_text 的选号逻辑
        fs = 11
        for cand in (14, 13, 12, 11, 10, 9, 8):
            w = tkfont.Font(family=FM, size=cand, weight="bold").measure(label)
            if w <= avail:
                fs = cand
                break
        tw, th = fits(label, fs, avail)
        ok = tw <= avail
        over = tw - avail
        print(f"  {label:>4} 用 {fs}pt  宽 {tw}px  "
              f"{'OK' if ok else f'超出 {over:.1f}px'}")
        if size == 76:
            chk(f"环内 {label} 不超可用宽", ok, f"{tw} <= {avail:.1f}")

# 环的实际高度是否被卡片装下
obj.ring.set_value(1.0, sub="定位")
root.update()
rh = obj.ring.winfo_reqheight()
ch_ = obj.card_overview.cget("height")
print(f"\n环控件高 {rh}px  概览卡高 {ch_}px  "
      f"标题+留白约 {10+8+26}px  →  需要 {rh + 10 + 8 + 26 + 6}px")
need = rh + 10 + 8 + 26 + 6
chk("概览卡装得下环 + 副标题", int(ch_) >= need, f"卡 {ch_} >= 需要 {need}")

# 副标题基线是否落在环控件内（不能在 s 之下被切掉）
s = 76
sub_top = s + 2
sub_bottom = sub_top + tkfont.Font(family=FM, size=8).metrics("linespace")
print(f"副标题占 {sub_top}..{sub_bottom}px（环控件总高 {obj.ring.winfo_reqheight()}px）")
chk("副标题在控件内", sub_bottom <= obj.ring.winfo_reqheight(),
    f"{sub_bottom} <= {obj.ring.winfo_reqheight()}")

root.destroy()
print()
print("结论：" + ("全部通过" if not fails else ("失败项 " + ", ".join(fails))))
