# -*- coding: utf-8 -*-
"""校验：行内单元格 x 坐标 与 表头 x 坐标 是否一一对齐。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import tkinter as tk

import app as A
import connscan

root = tk.Tk()
root.withdraw()
obj = A.App(root)
root.update()

obj._draw_header()
# 表头每列的起点
hx = []
x = 10
for text, key in [("应用 / 进程", "proc"), ("状态", "state"), ("服务器 IP", "ip"),
                  ("端口", "port"), ("PID", "pid"), ("国家/地区", "country"),
                  ("城市", "place"), ("运营商/机房", "net")]:
    hx.append((text, x))
    x += obj.widths[key]
print("表头列起点：")
for t, v in hx:
    print(f"  {t:<14} x={v}")

conns = connscan.collect(min_kind=connscan.NET_LAN, include_listening=False)
obj.conns = conns
obj.filtered = conns
obj.render_rows()
row = obj.rows[0]

names = ["进程(文字)", "状态", "IP", "端口", "PID", "国家", "城市", "运营商"]
print("\n行内单元格 x：")
rx = []
for i, cell in enumerate(row.cells):
    px = cell.place_info().get("x")
    w = cell.place_info().get("width")
    rx.append(int(px))
    print(f"  {names[i]:<10} x={px} w={w}")

# 进程列：文字起点应为 10+16+6=32，且不超出 proc 列范围
chk = []
chk.append(("进程文字起点=32", rx[0] == 32, rx[0]))
# 其余列应等于表头起点（表头首列 x=10，行内首列文字在 32，不比对首列）
for i in range(1, 8):
    ok = rx[i] == hx[i][1]
    chk.append((f"{names[i]} 对齐表头", ok, f"行={rx[i]} 表头={hx[i][1]}"))
# 进程列不能压到状态列
chk.append(("进程列不越界", rx[1] >= 10 + obj.widths["proc"],
            f"状态起点={rx[1]} 进程列结束={10 + obj.widths['proc']}"))

print()
for name, ok, extra in chk:
    print(("PASS " if ok else "FAIL ") + name + f"  ({extra})")
print()
print("结论：" + ("全部对齐" if all(c[1] for c in chk) else "存在错位"))
root.destroy()
