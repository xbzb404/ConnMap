"""确认「服务器 IP」列到底有没有被截断。

背景：用户截图里 IP 看着像 `115.231.184.`，但 Font.measure 算下来
98px 文字放进 130px 单元格绰绰有余。之前用物理像素坐标抓图（DPI 1.5x），
抓到的其实是一大段更宽的区域，反而看着像被切了。

做法：
  1) 开 DPI 感知，让 winfo_* 与 ImageGrab 处在同一套物理像素里；
  2) 直接问控件要 **IP 标签自己的** 屏幕坐标与宽度；
  3) 逐列扫描文字像素块，按「等宽字体下 IP 应合并成 4 组数字块」判断完整性；
  4) 同时打印 IP 控件右缘到端口控件左缘的间隔，确认不是被相邻列压住。
"""

import ctypes
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

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


def settle(root, inst, seconds=8.0):
    """手动驱动事件循环，等表格铺出第一行。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        if getattr(inst, "rows", None):
            break
        time.sleep(0.05)


def main():
    root = tk.Tk()
    inst = A.App(root)

    settle(root, inst, 2.0)
    # __init__ 里排了 after(120, self.scan)，事件循环跑起来后会自动开扫
    settle(root, inst, 12.0)

    root.deiconify()
    root.state("normal")
    root.lift()
    root.attributes("-topmost", True)
    for _ in range(6):
        root.update()
    time.sleep(0.4)
    root.update()

    rows = inst.rows
    print(f"行数 {len(rows)}")
    if not rows:
        print("FAIL 没有行，无法检查")
        return 1

    ipfont = tkfont.Font(font=rows[0].fonts["mono_sm"])
    ok = True
    checked = 0

    for r in rows:
        if len(r.cells) < 4:
            continue
        lbl = r.cells[2]       # 0=proc 1=state 2=ip 3=port
        port_lbl = r.cells[3]
        text = lbl.cget("text")
        if not text or "." not in text:
            continue
        checked += 1
        if checked > 6:
            break

        x = lbl.winfo_rootx()
        y = lbl.winfo_rooty()
        w = lbl.winfo_width()
        h = lbl.winfo_height()
        need = ipfont.measure(text)
        fits = need <= w

        gap = port_lbl.winfo_rootx() - (x + w)
        print(f"\n[{text}]  控件 x={x} y={y} w={w} h={h}")
        print(f"   Font.measure = {need}  → {'放得下' if fits else '放不下'}"
              f"   余量 {w - need}px")
        print(f"   到端口列的间隔 {gap}px")

        if not fits:
            ok = False
            print("   FAIL 文本比控件宽")
            continue

        # 抓「IP 控件左缘 → 端口控件左缘 + 40」这一段
        right = port_lbl.winfo_rootx() + 40
        try:
            im = ImageGrab.grab(bbox=(x, y, right, y + h))
        except Exception as e:  # noqa: BLE001
            print(f"   SKIP 抓图失败：{e}")
            continue
        im.save(os.path.join(SHOT_DIR, f"ipcol_{checked}.png"))

        rgb = im.convert("RGB")
        px = rgb.load()
        iw, ih = rgb.size
        cols = []
        for cx in range(iw):
            n = 0
            for cy in range(ih):
                rr, gg, bb = px[cx, cy]
                if rr + gg + bb < 450:
                    n += 1
            cols.append(n)

        runs, inrun, s = [], False, 0
        for cx, n in enumerate(cols):
            if n and not inrun:
                inrun, s = True, cx
            elif not n and inrun:
                inrun = False
                runs.append((s, cx - 1))
        if inrun:
            runs.append((s, iw - 1))

        merged = []
        for a, b in runs:
            if merged and a - merged[-1][1] <= 3:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))

        last = merged[-1][1] if merged else -1
        glyphs = ", ".join(f"{a}..{b}" for a, b in merged)
        print(f"   抓图 {rgb.size}  字块 {len(merged)} 组: {glyphs}")
        print(f"   最后一列有字像素 x={last}  右侧余白 {iw - 1 - last}px")

        # 等宽字体下 IP 应是 4 组数字（点会跟数字连成一组）
        if len(merged) < 4:
            ok = False
            print("   FAIL 字块少于 4 组，疑似被截断")
        else:
            print("   OK IP 完整")

    print(f"\n检查了 {checked} 行")
    print("结论：IP 列无截断" if ok else "结论：存在截断")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
