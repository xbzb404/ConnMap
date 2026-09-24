# -*- coding: utf-8 -*-
"""验证抗锯齿出图：把控件渲染结果 dump 成 PNG，并统计边缘过渡像素。"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)
import tkinter as tk
from PIL import Image, ImageGrab, ImageDraw

LOG = os.path.join(HERE, "_aa_test.log")

def w(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(str(m) + "\n")

try:
    import app as A
    w("PIL in app: %s  SS=%s" % (A._HAS_PIL, A.SS))
    root = tk.Tk()
    root.geometry("360x220+60+60")
    root.configure(bg=A.C["bg"])

    ring = A.Ring(root, size=90, bg=A.C["surface"])
    ring.pack(pady=10)
    ring.set_value(0.62, label="62%", sub="已定位")

    bar = A.Bar(root, height=10, bg=A.C["surface"])
    bar.pack(fill="x", padx=20, pady=8)
    bar.set(0.45, A.C["accent"])

    box = tk.Frame(root, bg=A.C["bg"]); box.pack()
    f = {"tiny": (A.FONT_FAMILY, 8), "small": (A.FONT_FAMILY, 9)}
    tb = A.Tag(box, "CDN", A.C["fail"], A.C["bg"], f); tb.pack(side="left", padx=4)
    dc = A.Chip(box, "仅公网连接", None, f, active=True); dc.pack(side="left", padx=4)
    dot = A.Dot(box, A.C["ok"], bg=A.C["bg"]); dot.pack(side="left", padx=6)

    for _ in range(20):
        root.update(); time.sleep(0.08)

    w("ring photo=%s" % (ring._photo is not None))
    w("bar  photo=%s" % (bar._photo is not None))
    w("tag  photo=%s" % (tb._photo is not None))
    w("chip photo=%s" % (dc._photo is not None))
    w("dot  photo=%s" % (dot._photo is not None))

    root.lift(); root.attributes("-topmost", True)
    for _ in range(4):
        root.update(); time.sleep(0.3)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ww, hh = root.winfo_width(), root.winfo_height()
    shot = os.path.join(ROOT, "_shots", "aa_probe.png")
    os.makedirs(os.path.dirname(shot), exist_ok=True)
    ImageGrab.grab(bbox=(x, y, x + ww, y + hh)).save(shot)
    w("grab ok %sx%s" % (ww, hh))

    im = Image.open(shot).convert("RGB")
    cols = im.getcolors(maxcolors=1 << 22) or []
    w("distinct colors = %d" % len(cols))
    # 抗锯齿的判据：出现「白色与强调色之间的中间色」
    acc, surf = A._rgb(A.C["accent"]), (255, 255, 255)
    def near(p, q, tol): return all(abs(a - b) <= tol for a, b in zip(p, q))
    mid = 0
    for n, col in cols:
        if near(col, surf, 6) or near(col, acc, 6):
            continue
        # 判断是否落在 surf→acc 的连线上（只受 alpha 混合影响）
        ok = True
        for i in range(3):
            lo, hi = sorted((surf[i], acc[i]))
            if not (lo - 12 <= col[i] <= hi + 12):
                ok = False; break
        if ok:
            mid += n
    w("anti-alias transition pixels = %d" % mid)
    w("结论: " + ("有抗锯齿过渡" if mid > 200 else "未检出抗锯齿"))
    root.destroy(); w("done")
except Exception:
    import traceback
    w("EXCEPTION:/n" + traceback.format_exc())
