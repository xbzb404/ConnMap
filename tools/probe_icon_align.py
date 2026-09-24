# -*- coding: utf-8 -*-
"""校验：所有行的图标必须落在完全相同的 x/y，与「真图标/兜底色块」无关。

曾经的 bug：兜底色块用 self.icon_lbl.winfo_x() 取位置，但那时还在
__init__ 里、控件尚未布局，读回恒为 0 → 兜底行比真图标行左移 10px，
同一列里两种图标错开，看起来就是「图标没对齐」。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import tkinter as tk

import app as A
import winproc

fails = []


def chk(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(name)


root = tk.Tk()
root.geometry("1360x870")
obj = A.App(root)
root.update()

conns = A.connscan.collect(min_kind=A.connscan.NET_LAN, include_listening=False)
obj.conns = conns
obj.filtered = conns
obj.render_rows()
root.update()

print(f"共 {len(obj.rows)} 行\n")

real_x, fall_x = set(), set()
real_y, fall_y = set(), set()
n_real = n_fall = 0

for row in obj.rows:
    path = (row.conn.proc_path or "").strip()
    has_real = bool(path) and winproc.extract_icon_rgba(path, row.ICON) is not None
    px = int(row.icon_lbl.place_info().get("x"))
    py = int(row.icon_lbl.place_info().get("y"))
    if has_real:
        real_x.add(px)
        real_y.add(py)
        n_real += 1
    else:
        fall_x.add(px)
        fall_y.add(py)
        n_fall += 1

print(f"真图标行 {n_real} 个，x 取值 {sorted(real_x) or '—'}，y 取值 {sorted(real_y) or '—'}")
print(f"兜底行   {n_fall} 个，x 取值 {sorted(fall_x) or '—'}，y 取值 {sorted(fall_y) or '—'}")

allx = real_x | fall_x
ally = real_y | fall_y
chk("所有行图标 x 一致", len(allx) == 1, f"出现 {sorted(allx)} 种 x")
if real_x and fall_x:
    chk("真图标与兜底图标同位", real_x == fall_x,
        f"真 {sorted(real_x)} vs 兜底 {sorted(fall_x)}")
else:
    print("（本轮只有一种图标来源，跳过同位对比）")
chk("所有行图标 y 一致", len(ally) == 1, f"出现 {sorted(ally)} 种 y")

# 图标不能被压在文字下面：图标右缘 <= 文字起点
bad = [r for r in obj.rows
       if int(r.icon_lbl.place_info().get("x")) + r.ICON
       > int(r.cells[0].place_info().get("x"))]
chk("图标不与文字重叠", not bad, f"{len(bad)} 行重叠")

# 文字起点也要一致
tx = {int(r.cells[0].place_info().get("x")) for r in obj.rows}
chk("所有行文字起点一致", len(tx) == 1, f"出现 {sorted(tx)} 种")

root.destroy()
print()
print("结论：" + ("全部通过" if not fails else ("失败项 " + ", ".join(fails))))
