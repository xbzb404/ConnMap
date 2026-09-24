# -*- coding: utf-8 -*-
"""验证「监听端口可见」与「端口 / 进程 / IP 三个独立筛选」。

背景：本机开的服务器（七日杀 26900 这类）原来是**看不到的**，三层原因叠加：
  1. `collect(include_listening=False)` 把 TCP LISTENING 全丢了；
  2. UDP 监听行的远端是 `*:*`，`split_hostport` 会把整串当主机名返回，
     于是分类落进 UNKNOWN，被范围过滤吃掉；
  3. 允许的类别里压根没有「监听」这一档。
本脚本就是给这三条各上一道锁。

分三段：
  A) 解析层（不需要界面）：`*:*` 的拆分、监听项判定、display_* 取值、
     三种范围档对监听项的收放；
  B) 界面层（要 Tk）：三个筛选框各自生效、可叠加、端口按精确匹配，
     以及监听行的 IP / 端口 / 状态三格显示的是本机地址而不是 `0.0.0.0 / 0`；
  C) 真机段：本机此刻若真有服务在听，确认它能出现在表里。

用法：python tools/probe_listen_filter.py      退出码 0 = 全部通过。
"""
import ctypes
import os
import sys
import time
import tkinter as tk

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:                                     # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import app as A                                        # noqa: E402
import connscan as CS                                  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def pump(root, want, limit_s=60, step=0.05):
    t0 = time.time()
    while time.time() - t0 < limit_s:
        root.update()
        if want():
            return True
        time.sleep(step)
    return False


# 一段**真实格式**的 netstat 输出（GBK 解码后的样子），用来跑真实的解析路径。
# 直接打桩 list_connections 会连「include_listening 的过滤」一起绕过，
# 那就测不出真实行为了 —— 所以桩打在更下层：喂文本给 _run。
FAKE_NETSTAT = """
  TCP    0.0.0.0:26900          0.0.0.0:0              LISTENING       19292
  UDP    0.0.0.0:26900          *:*                                    19292
  UDP    0.0.0.0:26902          *:*                                    19292
  UDP    [::]:26902             *:*                                    19292
  TCP    192.168.10.5:51234    104.18.125.108:443     ESTABLISHED     9288
  TCP    192.168.10.5:51235    192.168.10.5:8443      ESTABLISHED     16600
"""


def mk(proto, lip, lport, rip, rport, state, pid, name, kind, listen=False):
    return CS.Connection(
        proto=proto, local_ip=lip, local_port=lport,
        remote_ip=rip, remote_port=rport, state=state, pid=pid,
        proc_name=name, net_kind=kind, listen_only=listen)


# 一组合成连接：两条监听项（TCP/UDP 各一）+ 两条真正对外连的
def sample():
    return [
        mk("TCP", "0.0.0.0", 26900, "0.0.0.0", 0, "LISTENING", 19292,
           "7DaysToDie.exe", CS.NET_LISTEN, listen=True),
        mk("UDP", "0.0.0.0", 26902, "*", 0, "UDP", 19292,
           "7DaysToDie.exe", CS.NET_LISTEN, listen=True),
        mk("TCP", "192.168.10.5", 51234, "104.18.125.108", 443,
           "ESTABLISHED", 9288, "msedge.exe", CS.NET_PUBLIC),
        mk("TCP", "192.168.10.5", 51235, "192.168.10.5", 8443,
           "ESTABLISHED", 16600, "node.exe", CS.NET_LAN),
    ]


# ---------------------------------------------------------------- A. 解析层

def part_a():
    print("\n===== A. 解析层 =====")

    host, port = CS.split_hostport("*:*")
    check("UDP 的 `*:*` 拆成通配主机 + 0 端口", (host, port) == ("*", 0),
          f"实得 {(host, port)!r}")

    host, port = CS.split_hostport("[::]:26902")
    check("IPv6 `[::]:26902` 仍按括号解析", (host, port) == ("::", 26902),
          f"实得 {(host, port)!r}")

    host, port = CS.split_hostport("104.18.125.108:443")
    check("普通 `IP:端口` 不受影响", (host, port) == ("104.18.125.108", 443),
          f"实得 {(host, port)!r}")

    TCP_L, UDP_L, PUB, LAN = sample()

    check("TCP LISTENING 判为监听项", TCP_L.is_listen_only)
    check("UDP `*:*` 判为监听项", UDP_L.is_listen_only)
    check("对外连接的 ESTABLISHED 不是监听项", not PUB.is_listen_only)
    check("局域网内互连不是监听项", not LAN.is_listen_only)

    check("监听项 IP 列显示本机地址", TCP_L.display_ip == "0.0.0.0",
          TCP_L.display_ip)
    check("监听项端口列显示本地端口", TCP_L.display_port == 26900,
          str(TCP_L.display_port))
    check("监听项不会被当成「连到 *」", PUB.display_ip == "104.18.125.108",
          PUB.display_ip)
    check("非监听项端口仍取远端口", PUB.display_port == 443,
          str(PUB.display_port))

    # 范围档与过滤：桩打在 _run 上，走的是真实的 list_connections 解析，
    # 不依赖本机此刻恰好开着什么服务。
    real_run, real_procs = CS._run, CS.list_processes
    CS._run = lambda cmd, timeout=60: FAKE_NETSTAT
    CS.list_processes = lambda *a, **k: {}
    try:
        rows = CS.list_connections(include_listening=True)
        check("合成 netstat 的 6 行全部解析出来", len(rows) == 6, f"{len(rows)} 条")
        lis = [c for c in rows if c.is_listen_only]
        check("其中 4 行判为监听项（2×26900 + 2×26902）", len(lis) == 4,
              f"{len(lis)} 条")
        check("TCP 0.0.0.0:26900 在列",
              any(c.proto == "TCP" and c.display_port == 26900 for c in lis))
        check("UDP `*:*` 那几行没有被当主机名丢掉",
              sum(1 for c in lis if c.proto == "UDP") == 3,
              f"{sum(1 for c in lis if c.proto == 'UDP')} 条 UDP")

        off = CS.list_connections(include_listening=False)
        check("关掉监听后只剩有对端的两条", len(off) == 2, f"{len(off)} 条")

        pub = CS.collect(min_kind=CS.NET_PUBLIC, include_listening=True)
        check("仅公网档看不到监听项", all(not c.is_listen_only for c in pub),
              f"{len(pub)} 条")

        lan = CS.collect(min_kind=CS.NET_LAN, include_listening=True)
        check("含局域网档能看到本机监听口（★核心）",
              any(c.display_port == 26900 for c in lan), f"{len(lan)} 条")

        lan_off = CS.collect(min_kind=CS.NET_LAN, include_listening=False)
        check("关掉「显示监听端口」后监听项消失",
              all(not c.is_listen_only for c in lan_off), f"{len(lan_off)} 条")

        any_scope = CS.collect(min_kind=CS.NET_ANY, include_listening=True)
        check("含本机档收得比局域网档更全", len(any_scope) >= len(lan),
              f"{len(any_scope)} 条")
    finally:
        CS._run, CS.list_processes = real_run, real_procs

    check("26900 已登记端口语义", CS.port_hint(26900) == "七日杀 游戏端口",
          CS.port_hint(26900))
    check("七日杀进程有友好名",
          "七日杀" in CS.human_proc_name("7DaysToDie.exe"),
          CS.human_proc_name("7DaysToDie.exe"))


# ---------------------------------------------------------------- B. 界面层

def part_b(root, inst):
    print("\n===== B. 界面层（筛选 + 监听行显示） =====")

    # 输入框现在**不自动过滤**：改值只在「筛选」按钮上留个记号，回车或点按钮才
    # 真正应用。探针里直接改变量再断言会读到上一轮结果，所以把各变量的回调换成
    # 「立即过滤」—— 本段验的是筛选逻辑本身，触发时机是另一回事。
    # ⚠ 不能只替换 inst._queue_filter 之类的方法属性：trace 注册的是当初那个
    #   绑定方法，换实例属性对它毫无影响（踩过）。
    for var in (inst.search_var, inst.f_proc_var, inst.f_ip_var,
                inst.f_port_var, inst.show_resolved_var):
        for mode, cb in var.trace_info():
            var.trace_remove(mode, cb)
        var.trace_add("write", lambda *a: inst.apply_filter())

    inst.conns = sample()
    inst.filter_country = None
    inst.show_resolved_var.set(False)

    def reset():
        inst.f_proc_var.set("")
        inst.f_ip_var.set("")
        inst.f_port_var.set("")
        inst.search_var.set("")
        inst.apply_filter()

    reset()
    check("四条合成连接全部进表", len(inst.filtered) == 4, f"{len(inst.filtered)} 条")

    print("\n  -- 端口筛选 --")
    inst.f_port_var.set("26900")
    check("端口 26900 命中监听那条", len(inst.filtered) == 1, f"{len(inst.filtered)} 条")
    check("命中的确实是 TCP 26900",
          inst.filtered and inst.filtered[0].display_port == 26900)

    inst.f_port_var.set("443")
    check("端口 443 不会把 8443 一起捞出来（精确匹配）",
          len(inst.filtered) == 1 and inst.filtered[0].display_port == 443,
          f"{[c.display_port for c in inst.filtered]}")

    inst.f_port_var.set("269")
    check("非纯数字 / 不完整端口不误命中", len(inst.filtered) == 0,
          f"{len(inst.filtered)} 条")

    reset()
    print("\n  -- 进程筛选 --")
    inst.f_proc_var.set("7days")
    check("按 exe 名搜进程命中 2 条监听项", len(inst.filtered) == 2,
          f"{len(inst.filtered)} 条")
    inst.f_proc_var.set("七日杀")
    check("按中文友好名也能搜到同一批", len(inst.filtered) == 2,
          f"{len(inst.filtered)} 条")

    reset()
    print("\n  -- IP 筛选 --")
    inst.f_ip_var.set("104.18")
    check("按远端 IP 前缀命中", len(inst.filtered) == 1, f"{len(inst.filtered)} 条")
    inst.f_ip_var.set("0.0.0.0")
    check("监听项的本地监听地址也参与 IP 匹配（两条 0.0.0.0 都该命中）",
          len(inst.filtered) == 2, f"{len(inst.filtered)} 条")
    inst.f_ip_var.set("192.168.10.5")
    check("本机网卡地址也能命中（远端 / 本地两侧都参与匹配）",
          len(inst.filtered) == 2, f"{len(inst.filtered)} 条")

    reset()
    print("\n  -- 条件叠加 --")
    inst.f_proc_var.set("7days")
    inst.f_port_var.set("26902")
    check("进程 + 端口 叠加后只剩 1 条", len(inst.filtered) == 1,
          f"{len(inst.filtered)} 条")
    inst.f_port_var.set("443")
    check("叠加后无交集则为空", len(inst.filtered) == 0, f"{len(inst.filtered)} 条")

    inst.clear_filter()
    check("清除全部筛选后回到 4 条", len(inst.filtered) == 4, f"{len(inst.filtered)} 条")

    print("\n  -- 监听行的单元格文字 --")
    inst.apply_filter()
    row = None
    for r in inst.rows:
        if r.conn.display_port == 26900:
            row = r
            break
    check("表格里能找到那条监听行", row is not None)
    if row is not None:
        texts = [lbl.cget("text") for lbl in row.cells]
        check("IP 格显示 0.0.0.0（不是空的远端）", texts[2] == "0.0.0.0", texts[2])
        check("端口格显示 26900（不是 0）", texts[3] == "26900", texts[3])
        check("状态格带协议便于区分 TCP/UDP", texts[1] == "监听 TCP", texts[1])
        check("进程格是友好名", "七日杀" in texts[0], texts[0])

    inst.conns = []


# ---------------------------------------------------------------- C. 真机段

def part_c():
    print("\n===== C. 真机段 =====")
    conns = CS.collect(min_kind=CS.NET_LAN, include_listening=True)
    listeners = [c for c in conns if c.is_listen_only]
    print(f"  本机当前 {len(conns)} 条连接，其中监听项 {len(listeners)} 条")
    for c in listeners[:10]:
        tag = c.service or ""
        print(f"      {c.proto:<4}{c.display_ip}:{c.display_port:<8}"
              f"{CS.human_proc_name(c.proc_name):<22}{tag}")
    check("枚举确实带回了监听项", bool(listeners), f"{len(listeners)} 条")
    return conns


def main():
    part_a()
    root = tk.Tk()
    inst = A.App(root)
    # 必须等首轮扫描真的跑完再注入合成数据：扫描是后台线程，
    # 它的回调会把 inst.conns 覆盖回真机结果，抢在它前面注入等于白注。
    pump(root, lambda: inst.worker is not None and not inst.worker.is_alive(),
         limit_s=40)
    pump(root, lambda: bool(inst.conns), limit_s=5)
    try:
        part_b(root, inst)
    finally:
        part_c()
        try:
            root.destroy()
        except tk.TclError:
            pass

    print("\n" + "=" * 62)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：")
        for f in FAILS:
            print(f"   - {f}")
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
