# -*- coding: utf-8 -*-
"""验证「点表头排序」。

两段：
  A) 跑真机扫描 —— 验证点击坐标反查列、提示行、表头箭头渲染；
  B) 注入一组构造好的连接 —— 确定性验证排序语义（数值 IP、缺失沉底、
     升/降序、三档循环），不依赖本机当前恰好有哪些连接。

用法：python tools/probe_header_sort.py     退出码 0 = 全部通过。
"""
import ctypes
import os
import sys
import time
import tkinter as tk
import traceback

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:                                     # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app as A                                        # noqa: E402
import connscan                                        # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def pump(root, tag, want, limit_s=60, step=0.05):
    """带真实 sleep 地抽事件队列 —— 只用 update() 不给 after/线程机会。"""
    t0 = time.time()
    while time.time() - t0 < limit_s:
        root.update()
        if want():
            return True
        time.sleep(step)
    print(f"  ({tag} 等到 {limit_s}s 仍未满足条件)")
    return False


def asc(seq):
    return all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))


def desc(seq):
    return all(seq[i] >= seq[i + 1] for i in range(len(seq) - 1))


# ---------------------------------------------------------------- 合成数据

def mk(ip, port, pid, proc, state="ESTABLISHED", province=None,
       lat=None, lon=None):
    geo = {}
    if province is not None:
        geo = {"ok": True, "country": "中国", "province": province,
               "city": province + "市", "district": "", "isp": "测试ISP",
               "org": f"{province}测试机房", "lat": lat, "lon": lon}
    return connscan.Connection("TCP", "192.168.1.10", 50000, ip, port,
                               state, pid, proc_name=proc,
                               net_kind=connscan.NET_PUBLIC, geo=geo)


SYN = [
    mk("10.0.0.2", 443, 101, "chrome.exe", province="广东", lat=23.1, lon=113.3),
    mk("9.1.1.1", 80, 102, "chrome.exe", province="北京", lat=39.9, lon=116.4),
    mk("192.168.1.7", 8080, 103, "git.exe", province="浙江", lat=30.2, lon=120.1),
    mk("2001:db8::1", 443, 104, "node.exe"),          # IPv6 + 无地理信息
    mk("172.16.0.9", 53, 105, "svc.exe"),             # 无地理信息
]


def main():
    root = tk.Tk()
    inst = A.App(root)
    # 集成段不需要联网查地理，关掉以免等待（合成段自带 geo）
    inst.auto_geo_var.set(False)

    print("\n===== A. 真机扫描段 =====")
    got = pump(root, "扫描", lambda: len(inst.conns) > 0, limit_s=60)
    print(f"  扫描到 {len(inst.conns)} 条连接，表内 {len(inst.filtered)} 行")
    check("扫描拿到连接", got and len(inst.conns) > 0)

    check("初始未排序", inst.sort_col is None and inst.sort_desc is False)
    hint = inst.tbl_hint.cget("text")
    check("提示行给了可点线索", "点表头可排序" in hint, hint)

    print("\n  -- 点击坐标反查列（10 列逐一点） --")
    for idx, (text, key) in enumerate(A.App.COLS):
        x_canvas = 10 + sum(inst.widths[k] for _t, k in A.App.COLS[:idx]) + 40
        offset = inst.header.canvasx(0)

        class Ev:
            pass
        ev = Ev()
        ev.x = int(x_canvas - offset)
        inst.sort_col, inst.sort_desc = None, False
        inst._on_header_click(ev)
        check(f"点第 {idx + 1} 列「{text}」→ {key}", inst.sort_col == key,
              f"实际 {inst.sort_col}")
    inst.sort_col, inst.sort_desc = None, False
    # 表头只在 _draw_header() 时才反映排序状态，apply_filter() 不碰表头。
    # 只改属性不重绘，表头会留着上一列的箭头 —— 这里曾因此误报
    # 「未排序时无箭头」失败（真实使用走 cycle_sort，它自己会重绘）。
    inst._draw_header()
    inst.apply_filter()

    print("\n  -- 表头箭头渲染 --")

    def head_texts():
        return [inst.header.itemcget(i, "text")
                for i in inst.header.find_all()
                if inst.header.type(i) == "text"]

    check("未排序时无箭头", all("↑" not in t and "↓" not in t for t in head_texts()))
    inst.sort_col, inst.sort_desc = "province", False
    inst._draw_header()
    check("升序画 ↑", any("↑" in t for t in head_texts()), str(head_texts()))
    inst.sort_col, inst.sort_desc = "net", True
    inst._draw_header()
    net_txt = " ".join(head_texts())
    print(f"       net 列实际绘制：{net_txt[-26:]}")
    check("net 列标题没被箭头挤掉", "运营商 / 机房（全称）" in net_txt)
    inst.sort_col, inst.sort_desc = None, False
    inst._draw_header()

    print("\n===== B. 合成数据段（确定性排序语义） =====")
    inst.conns = list(SYN)
    inst.filter_country = None
    inst.search_text = ""
    inst.sort_col, inst.sort_desc = None, False
    inst.apply_filter()
    check("注入 5 条全部进表", len(inst.filtered) == 5, str(len(inst.filtered)))

    print("\n  -- IP：数值序，不是字典序；IPv6 沉底 --")
    inst.cycle_sort("ip")
    got = [c.remote_ip for c in inst.filtered]
    want = ["9.1.1.1", "10.0.0.2", "172.16.0.9", "192.168.1.7", "2001:db8::1"]
    check("IP 升序 = 数值序 + IPv6 末尾", got == want, f"{got}")
    inst.cycle_sort("ip")
    got = [c.remote_ip for c in inst.filtered]
    # IPv6 视为「无该列值」，按设计升/降序都沉底，所以只逆序前四条
    check("IP 降序 = 四条 IPv4 逆序 + IPv6 仍在末尾",
          got == want[:4][::-1] + ["2001:db8::1"], f"{got}")
    inst.cycle_sort("ip")
    check("三档循环回到未排序", inst.sort_col is None)

    print("\n  -- 端口：数值升降序 --")
    inst.cycle_sort("port")
    got = [c.remote_port for c in inst.filtered]
    check("端口升序", got == [53, 80, 443, 443, 8080], str(got))
    inst.cycle_sort("port")
    got = [c.remote_port for c in inst.filtered]
    check("端口降序", got == [8080, 443, 443, 80, 53], str(got))
    inst.cycle_sort("port")

    print("\n  -- PID --")
    inst.cycle_sort("pid")
    got = [c.pid for c in inst.filtered]
    check("PID 升序", got == [101, 102, 103, 104, 105], str(got))
    inst.cycle_sort("pid")
    got = [c.pid for c in inst.filtered]
    check("PID 降序", got == [105, 104, 103, 102, 101], str(got))
    inst.cycle_sort("pid")

    print("\n  -- 应用名（按列里显示的名字排，不是原始 exe 名） --")
    inst.cycle_sort("proc")
    shown = [c.scan_proc_display().lower() for c in inst.filtered]
    raw = [c.proc_name for c in inst.filtered]
    check("应用升序：显示名有序", asc(shown), f"{shown}")
    print(f"       显示名：{shown}")
    print(f"       原始名：{raw}")
    inst.cycle_sort("proc")
    shown_desc = [c.scan_proc_display().lower() for c in inst.filtered]
    check("应用降序：显示名逆序", desc(shown_desc), f"{shown_desc}")
    inst.cycle_sort("proc")

    print("\n  -- 省：缺地理信息的行升/降序都沉底 ★关键 --")
    inst.cycle_sort("province")
    got = [((c.geo or {}).get("province") or "（缺）") for c in inst.filtered]
    check("省升序 + 缺信息在尾部",
          got[:3] == ["北京", "广东", "浙江"] and got[3:] == ["（缺）", "（缺）"],
          str(got))
    inst.cycle_sort("province")               # 降序
    got = [((c.geo or {}).get("province") or "（缺）") for c in inst.filtered]
    check("省降序后前三条反转、缺信息仍在尾部 ★",
          got[:3] == ["浙江", "广东", "北京"] and got[3:] == ["（缺）", "（缺）"],
          str(got))
    inst.cycle_sort("province")

    print("\n  -- 经纬度：按纬度，缺坐标沉底 --")
    inst.cycle_sort("coord")
    got = [(c.geo or {}).get("province") or "（缺）" for c in inst.filtered]
    check("纬度升序 = 广东(23.1) 浙江(30.2) 北京(39.9) + 缺坐标末尾",
          got[:3] == ["广东", "浙江", "北京"] and got[3:] == ["（缺）", "（缺）"],
          str(got))
    inst.cycle_sort("coord")
    got = [(c.geo or {}).get("province") or "（缺）" for c in inst.filtered]
    check("纬度降序后前三条反转、缺坐标仍在尾部",
          got[:3] == ["北京", "浙江", "广东"] and got[3:] == ["（缺）", "（缺）"],
          str(got))
    inst.cycle_sort("coord")

    print("\n  -- 运营商/机房列 --")
    inst.cycle_sort("net")
    procs = [c.proc_name for c in inst.filtered]
    check("net 列可排且不丢行", len(procs) == 5, str(procs))
    hint = inst.tbl_hint.cget("text")
    check("提示行显示按「运营商 / 机房（全称）」升序",
          "按「运营商 / 机房（全称）」升序" in hint, hint)
    inst.cycle_sort("net")

    print("\n  -- 状态列：语义序（已连接在最前） --")
    inst.conns = [
        mk("1.1.1.1", 443, 201, "a.exe", state="TIME_WAIT"),
        mk("1.1.1.2", 443, 202, "b.exe", state="LISTENING"),
        mk("1.1.1.3", 443, 203, "c.exe", state="ESTABLISHED"),
        mk("1.1.1.4", 443, 204, "d.exe", state="SYN_SENT"),
    ]
    inst.cycle_sort("state")
    got = [c.state for c in inst.filtered]
    check("状态升序 = 已连接→握手→监听→其余",
          got == ["ESTABLISHED", "SYN_SENT", "LISTENING", "TIME_WAIT"], str(got))
    inst.cycle_sort("state")

    print("\n  -- 排序与筛选共存 --")
    inst.conns = list(SYN)
    inst.search_var.set("chrome")
    inst.apply_filter()
    inst.cycle_sort("port")
    check("筛选后仍只剩 2 条", len(inst.filtered) == 2, str(len(inst.filtered)))
    check("筛选 + 排序：端口升序", [c.remote_port for c in inst.filtered] == [80, 443],
          str([c.remote_port for c in inst.filtered]))
    inst.search_var.set("")
    inst.apply_filter()

    root.destroy()
    print("\n" + "=" * 62)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print("   -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                 # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
