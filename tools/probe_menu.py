# -*- coding: utf-8 -*-
"""验证：PID 列、图标列、右键菜单 的接线是否完整。"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import tkinter as tk

import app as A
import connscan
import winproc

fail = []


class Ev(object):
    x_root = 100
    y_root = 100



def chk(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + extra) if extra else ""))
    if not ok:
        fail.append(name)


root = tk.Tk()
root.withdraw()
try:
    obj = A.App(root)
except Exception:
    traceback.print_exc()
    raise SystemExit(1)

# 1) 列宽
chk("widths 含 pid", "pid" in obj.widths, repr(obj.widths))

# 2) 表头包含 PID
obj._draw_header()
texts = [obj.header.itemcget(i, "text")
         for i in obj.header.find_all()
         if obj.header.type(i) == "text"]
chk("表头含 PID", "PID" in texts, repr(texts))
# 顺序：端口 之后、国家/地区 之前
try:
    ip_i = texts.index("PID")
    chk("PID 列位置正确", texts.index("端口") < ip_i < texts.index("国家/地区"))
except ValueError:
    chk("PID 列位置正确", False, repr(texts))

# 3) 真实连接 + 真行渲染
try:
    conns = connscan.collect(min_kind=connscan.NET_LAN, include_listening=False)
except Exception:
    traceback.print_exc()
    conns = []
chk("拿到连接", bool(conns), f"{len(conns)} 条")

if conns:
    obj.conns = conns
    obj.filtered = conns
    obj.render_rows()
    chk("渲染出行", len(obj.rows) == len(conns), f"{len(obj.rows)} 行")
    r = obj.rows[0]
    chk("ConnRow 有 icon_lbl", hasattr(r, "icon_lbl"))
    chk("ConnRow 有 on_context", r.on_context is not None)
    chk("ConnRow 单元格数 = 8", len(r.cells) == 8, str(len(r.cells)))
    # PID 单元格显示的就是 pid
    chk("PID 单元格文本正确",
        r.cells[4].cget("text") == str(r.conn.pid),
        f"{r.cells[4].cget('text')} vs {r.conn.pid}")

    # 4) 右键菜单
    try:
        m = tk.Menu(root, tearoff=0)
        # 直接调 handler，但把 tk_popup 换掉，避免真弹窗
        real_popup = tk.Menu.tk_popup
        tk.Menu.tk_popup = lambda self, x, y: None
        try:
            obj.show_row_menu(r, Ev())
            chk("show_row_menu 不抛异常", True)
        finally:
            tk.Menu.tk_popup = real_popup
    except Exception:
        traceback.print_exc()
        chk("show_row_menu 不抛异常", False)

    # 路径覆盖情况
    withpath = sum(1 for c in conns if (c.proc_path or "").strip())
    chk("有路径的连接", withpath > 0, f"{withpath} / {len(conns)}")

    # 图标抽取
    paths = []
    seen = set()
    for c in conns:
        p = (c.proc_path or "").strip()
        if p and p not in seen:
            seen.add(p)
            paths.append(p)
    okn = 0
    for p in paths:
        if winproc.extract_icon_rgba(p, 16):
            okn += 1
    chk("图标可用", okn > 0, f"{okn} / {len(paths)} 个唯一进程")
else:
    chk("渲染出行", False, "无连接，跳过")

# 5) 菜单项文本（含 disabled 分支）
try:
    class FakeConn:
        proc_name = "chrome.exe"
        pid = 4321
        remote_hostport = "1.2.3.4:443"
        proc_path = ""

    class FakeRow:
        conn = FakeConn()
    real_popup = tk.Menu.tk_popup
    captured = {}


    def spy(self, x, y):
        captured["menu"] = self

    tk.Menu.tk_popup = spy
    try:
        obj.show_row_menu(FakeRow(), Ev())
    finally:
        tk.Menu.tk_popup = real_popup
    mm = captured.get("menu")
    labels = []
    if mm is not None:
        n = mm.index("end")
        for i in range((n or 0) + 1):
            try:
                labels.append(mm.entrycget(i, "label"))
            except tk.TclError:
                labels.append("<sep>")
    chk("无路径时禁用定位项", any("无路径信息" in L for L in labels), repr(labels))
except Exception:
    traceback.print_exc()
    chk("无路径时禁用定位项", False)

root.destroy()
print()
print("结论：" + ("全部通过" if not fail else ("失败项 " + ", ".join(fail))))
