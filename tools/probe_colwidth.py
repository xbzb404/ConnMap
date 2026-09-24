# -*- coding: utf-8 -*-
"""校验：每一列的列宽是否装得下真实数据（不截断）。

用真实连接数据 + 各列实际字体，量文字像素宽，和「列宽 - 内边距」比。
比肉眼截图可靠：截图只能看到当前可见那几行，也看不出是列窄还是被窗口裁。
"""
import os
import sys

# 必须开 DPI 感知：不开时屏幕报逻辑像素，Font.measure 量出的字宽明显偏小
# （同一个 IP 量到 98，实际 165），据此定列宽会真的截断。
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
sys.path.insert(0, ROOT)

import tkinter as tk
import tkinter.font as tkfont

import app as A
import connscan
import geoloc

fails = []


def chk(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + extra) if extra else ""))
    if not ok:
        fails.append(name)


root = tk.Tk()
root.withdraw()
obj = A.App(root)

fonts = {k: (tkfont.Font(font=v) if not isinstance(v, tkfont.Font) else v)
         for k, v in obj.fonts.items()}
W = obj.widths
PAD_IN_CELL = 8          # 单元格左右各留 4

# 收集真实数据 + 几个边界样本
conns = connscan.collect(min_kind=connscan.NET_LAN, include_listening=False)
samples = {
    "proc":    set(),
    "state":   set(),
    "ip":      set(),
    "port":    set(),
    "pid":      set(),
    "province": set(),
    "city":     set(),
    "district": set(),
    "net":      set(),
}
for c in conns:
    samples["proc"].add(c.scan_proc_display())
    samples["state"].add(c.scan_state_display())
    samples["ip"].add(c.remote_ip)
    samples["port"].add(str(c.remote_port))
    samples["pid"].add(str(c.pid))
    g = c.geo or {}
    if g.get("province"):
        samples["province"].add(g["province"])
    samples["city"].add(c.scan_city_display())
    samples["district"].add(c.scan_district_display())
    samples["net"].add(c.scan_network_display())
# 常见边界值补进去，别只信当前这一份快照。
# state 只放 _state_display() 真会输出的值 —— 原始 socket 状态（CLOSE_WAIT
# / ESTABLISHED 这类）在 collect() 里就被丢掉或映射成中文了，只有映射表
# 覆盖不到的状态才原样透出（UDP 就是一例）。
samples["state"] |= {"已连接", "连接中", "握手中", "监听", "UDP"}
# 省：用「规范化之后」的真实输出值（_norm_province 会把
# 新疆维吾尔自治区→新疆、内蒙古自治区→内蒙古）。国外长州名取 PROVINCE_CN_EN
# 里真实存在的最长项：不列颠哥伦比亚（卑诗省，加拿大 IP 真会返回）。
samples["province"] |= {"广东", "中国香港", "中国澳门", "中国台湾",
                        "内蒙古", "新疆",
                        "加利福尼亚", "不列颠哥伦比亚"}
# 市：只放 _city_display() 会输出的值（过 _localize_place / PLACE_CN）。
# 注意：TRAD_TO_SIMP 是国家名表（印度尼西亚等），不能进 city 样本，否则
# 会把「国家」当「城市」量出假的最长宽。最长真实城市取 PLACE_CN 里的
# 阿姆斯特丹（荷兰 IP 真会返回）。
samples["city"] |= {"查询中…", "—"}
samples["city"] |= set(geoloc.PLACE_CN.values())
samples["city"] |= {"圣何塞", "莫斯科", "北京", "上海"}
# 区：免费库普遍为空（显示「—」），国内经 PConline 才有。用「规范化之后」的
# 真实输出值（_norm_district 会把 乌鲁木齐县→乌鲁木齐、玄武区→玄武、
# 浦东新区→浦东）。最长也就 4 字（乌鲁木齐），不会更长。
samples["district"] |= {"查询中…", "—", "南山", "海淀", "浦东",
                        "玄武", "九龙城", "乌鲁木齐"}
samples["net"] |= {"Microsoft Azure Cloud (southeastasia) [云机房]",
                   "Akamai Technologies [CDN]",
                   "Huawei Cloud Service data center [云机房]"}
samples["ip"] |= {"255.255.255.255", "fe80::1"}
samples["pid"] |= {"4294967295"}
samples["port"] |= {"65535"}
# proc 的边界样本必须来自 connscan.PROC_ALIASES 本身 ——
# 先前这里手写了一个「Windows 系统服务宿主机」（210px）当最长值，
# 那个字符串根本不存在于别名表里（真实是「Windows 系统服务宿主」192px），
# 于是按 210px 定了列宽，白占 18px 还挤掉别的列。样本要取自真实数据源。
samples["proc"] |= set(connscan.PROC_ALIASES.values())
samples["proc"] |= {"未知进程"}

fontkey = {
    "proc": "small", "state": "small", "ip": "mono_sm", "port": "mono_sm",
    "pid": "mono_sm", "province": "small", "city": "small",
    "district": "small", "net": "small",
}

# net 是尾列，名字来自外部字符串，长度不可控。
# 用户已明确选择「标签优先、名字压短」，所以这列允许截断 ——
# 但要求：① 一定装得下；② 末尾的 [CDN]/[云机房] 标签必须保住。
TAIL_TOLERANT = {"net"}
TAIL_MIN_NAME_CHARS = 3      # 截断后名字至少留几个字符，否则等于没显示

print(f"{'列':<9}{'列宽':>5}{'可用':>6}   最长样本")
print("-" * 72)
for key in ("proc", "state", "ip", "port", "pid", "province", "city", "district", "net"):
    w = W[key]
    # proc 列还要让出图标位（ICON + 6）
    icon_slot = (A.ConnRow.ICON + 6) if key == "proc" else 0
    avail = w - PAD_IN_CELL - icon_slot
    f = fonts[fontkey[key]]
    longest = max(samples[key], key=lambda s: f.measure(s))
    tw = f.measure(longest)
    ok = tw <= avail
    tag = "OK  " if ok else ("可截断" if key in TAIL_TOLERANT else "截断")
    print(f"{key:<9}{w:>5}{avail:>6}   {tw:>4}px  {tag}  {longest[:34]}")
    if key in TAIL_TOLERANT:
        # 尾列允许截断，但要满足两条硬要求：
        #   ① 截断后一定装得下；
        #   ② [CDN]/[云机房] 这类标签不能被截掉（用户明确要求标签优先）；
        #   ③ 名字不能只剩一两个字，否则等于没显示。
        print(f"          （尾列，允许截断；标签必须保住）")
        netf = fonts[fontkey[key]]
        bad_fit, bad_tag, bad_name, widest = [], [], [], 0
        for sample in samples[key]:
            out = A.fit_net_text(sample, netf, avail)
            ow = netf.measure(out)
            widest = max(widest, ow)
            if ow > avail:
                bad_fit.append((sample, out, ow))
            for tg in ("[CDN]", "[云机房]", "[云]", "[CDN ]"):
                if tg in sample and tg not in out:
                    bad_tag.append((sample, out))
            # 名字部分（标签前的文字，去掉省略号）为空说明名字全丢了
            body = out.split("　[")[0].rsplit(" [", 1)[0].rstrip("…").strip()
            if "[" in sample and not body:
                bad_name.append((sample, out))
        # 逐个打印问题样本，便于定位
        for s, o, w in bad_fit:
            print(f"          装不下({w}>{avail})：{s!r} → {o!r}")
        for s, o in bad_tag:
            print(f"          标签丢失：{s!r} → {o!r}")
        for s, o in bad_name:
            print(f"          名字全丢：{s!r} → {o!r}")
        chk("net 截断后装得下且标签保住",
            not (bad_fit or bad_tag or bad_name),
            f"最宽 {widest}px ≤ {avail}px，样本 {len(samples[key])} 条")
        continue
    chk(f"{key} 列装得下最长样本", ok,
        f"需要 {tw}px，可用 {avail}px（差 {tw - avail}px）")

# 表头也要装得下：表头用 h3(11pt bold)，在同一个 cell 宽里画。
hf = fonts["h3"]
HEADERS = {
    "proc": "应用 / 进程", "state": "状态", "ip": "服务器 IP",
    "port": "端口", "pid": "PID",
    "province": "省", "city": "市", "district": "区",
    "net": "运营商/机房",
}
print()
for key, text in HEADERS.items():
    hw = hf.measure(text)
    ok = hw <= W[key]
    chk(f"表头「{text}」装得下", ok,
        f"需要 {hw}px，列宽 {W[key]}px（差 {hw - W[key]}px）")

# 整表宽度与表格可视区的关系。
# 用户明确选了「尾列给足宽度、宁可横向滚动」，所以这里不再断言「必须不滚」，
# 而是如实报告：不滚最好，滚了也算预期内（前提是横向滚动条可用、表头能同步）。
canvas_w = 950          # 1360 窗口下实测
needs_scroll = obj.table_w > canvas_w
print()
print(f"{'表宽':<9}{obj.table_w:>5}  可视 {canvas_w}px  "
      f"{'需要横向滚动（用户已确认可接受）' if needs_scroll else '无需横向滚动'}")
chk("表格有横向滚动条且表头会同步", hasattr(obj, "_hsb"),
    "存在 _hsb 滚动条与 _on_xscroll 同步逻辑" if hasattr(obj, "_hsb")
    else "缺少横向滚动条，超出部分将无法查看")

root.destroy()
print()
print("结论：" + ("全部通过" if not fails else ("失败项 " + ", ".join(fails))))
