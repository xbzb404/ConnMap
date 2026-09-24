# -*- coding: utf-8 -*-
"""本机代理检测：查出「这台机器上到底有没有代理、指向哪、是谁在提供」。

为什么不能只读一处注册表
------------------------
Windows 的「局域网设置」把同一份代理配置**存了两处**：

1. 传统值：HKCU\\...\\Internet Settings 下的
   ``ProxyEnable`` / ``ProxyServer`` / ``ProxyOverride`` / ``AutoConfigURL``。
   绝大多数排查脚本、包括很多「网络检测」工具读的都是这份。

2. 二进制块：同一个键下的 ``Connections\\DefaultConnectionSettings``
   （以及备份用的 ``SavedLegacySettings``），是一段
   ``INTERNET_PER_CONN_OPTION`` 序列：``<III`` 头（版本 / 写入计数 / 标志位）
   后面跟若干组「长度 + 内容」的字符串字段（代理服务器、绕过列表、PAC 地址）。

**Chromium / Edge / IE 实际读的是第 2 份**。两处不同步时，就会出现本文档开头
那个经典现象：注册表和「设置」界面都显示代理已启用，浏览器却在直连。

所以这里的做法是两处都读、对拍，并明确标出以哪一处为准（浏览器口径 = 二进制块）。

附带还查三件事：
  · 进程环境变量里的 HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY；
  · 本机环回地址上正在监听的那些常见代理端口（用 netstat，不主动连）；
  · 正在运行的代理客户端进程（Clash / v2rayN / sing-box / mihomo 等）。

只读不改：不写注册表、不发通知、不启停任何进程。
"""
from __future__ import annotations

import os
import platform
import struct
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import connscan

IS_WINDOWS = platform.system() == "Windows"

INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
CONNECTIONS_KEY = INTERNET_SETTINGS + r"\Connections"

# DefaultConnectionSettings 的标志位。位含义来自 WinINET 的
# INTERNET_PER_CONN_FLAGS，实测 ProxyEnable=1 时值为 0x03（0x01|0x02）。
FLAG_DIRECT = 0x01        # 直连（这一位平时一直置着，不代表「没代理」）
FLAG_PROXY = 0x02         # 为局域网使用代理服务器
FLAG_AUTODETECT = 0x04    # 自动检测设置
FLAG_PAC = 0x08           # 使用自动配置脚本

# 常见代理客户端监听口。不主动连接，只用来匹配 netstat 的监听项。
PROXY_PORTS = (
    7890, 7891, 7892, 7897, 7898,          # Clash / mihomo 系列
    10808, 10809, 10810, 10811,            # v2rayN / v2ray 系列
    1080, 1081, 1086, 1087,                # SOCKS
    2080, 2081, 2082,                      # 部分机场客户端
    20171, 20172,                          # v2rayN 旧版
    8889, 8888, 8080, 8118, 12333, 33210, 4780,
)

# 代理客户端进程名（小写）→ 显示名。用于「本机在跑什么代理」。
PROXY_APPS = {
    "clash.exe": "Clash",
    "clash-win64.exe": "Clash",
    "clash-verge.exe": "Clash Verge",
    "clash-verge-service.exe": "Clash Verge 服务",
    "verge-mihomo.exe": "Mihomo 内核",
    "mihomo.exe": "Mihomo 内核",
    "nextin.mihomohost.exe": "Mihomo 代理内核",
    "v2rayn.exe": "v2rayN",
    "v2ray.exe": "v2ray 内核",
    "xray.exe": "Xray 内核",
    "sing-box.exe": "sing-box 内核",
    "naive.exe": "NaiveProxy",
    "trojan.exe": "Trojan",
    "shadowst.exe": "Shadowsocks",
    "ss-local.exe": "Shadowsocks",
    "shadowsocksr.exe": "ShadowsocksR",
    "hysteria.exe": "Hysteria",
    "tuic.exe": "TUIC",
    "winsw.exe": "代理服务封装",
    "netch.exe": "Netch",
    "sstap.exe": "SSTap",
    "proxifier.exe": "Proxifier",
    "fiddler.exe": "Fiddler 抓包代理",
    "charles.exe": "Charles 抓包代理",
    "mitmproxy.exe": "mitmproxy",
    "burpsuite.exe": "Burp Suite",
}

# 环境变量里可能出现的代理变量名（大小写两种都认）
_ENV_NAMES = (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
    "NO_PROXY", "no_proxy",
)


# ---------------------------------------------------------------- 二进制块解析


def _parse_wininet_blob(blob: bytes):
    """解析 ``INTERNET_PER_CONN_OPTION`` 的持久化格式。

    布局：``<I`` 版本 + ``<I`` 写入计数 + ``<I`` 标志位，
    之后依次是三组「``<I`` 字节数 + 内容」（代理服务器 / 绕过列表 / PAC 地址）。
    字符串按 GBK 解（中文系统写进去的是本地 ANSI 编码）。

    解析不出来返回 None —— 调用方按「这块不可用」处理，不要让它影响结论。
    """
    if not blob or len(blob) < 16:
        return None
    try:
        version, counter, flags = struct.unpack_from("<III", blob, 0)
    except struct.error:
        return None
    off = 12
    out = {"version": version, "counter": counter, "flags": flags}
    for name in ("proxy_server", "bypass", "pac_url"):
        if off + 4 > len(blob):
            out[name] = ""
            continue
        (size,) = struct.unpack_from("<I", blob, off)
        off += 4
        if size <= 0 or off + size > len(blob):
            out[name] = ""
            continue
        raw = blob[off:off + size]
        off += size
        out[name] = raw.decode("gbk", "replace").rstrip("\x00").strip()
    return out


def _reg_bytes(root, path, name):
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except Exception:  # noqa: BLE001
        return None


def _reg_value(root, path, name):
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except Exception:  # noqa: BLE001
        return None


def _flags_text(flags: int) -> str:
    bits = []
    if flags & FLAG_DIRECT:
        bits.append("直连")
    if flags & FLAG_PROXY:
        bits.append("使用代理服务器")
    if flags & FLAG_AUTODETECT:
        bits.append("自动检测设置")
    if flags & FLAG_PAC:
        bits.append("自动配置脚本")
    return " + ".join(bits) or "（无标志）"


def read_wininet():
    """读出 WinINET 的两份代理存储，并给出对拍结果。

    返回 dict，字段：
      legacy_enabled / legacy_server / legacy_bypass / legacy_pac
      binary_enabled / binary_server / binary_bypass / binary_pac / binary_flags
      conflict  —— "" | "legacy_only" | "binary_only" | "server_diff"
      effective_*  —— 以二进制块为准的那一份
    """
    out = {
        "legacy_enabled": False, "legacy_server": "", "legacy_bypass": "",
        "legacy_pac": "",
        "binary_enabled": False, "binary_server": "", "binary_bypass": "",
        "binary_pac": "", "binary_flags": 0, "binary_parsed": False,
        "conflict": "", "note": "",
    }
    if not IS_WINDOWS:
        return out

    import winreg  # type: ignore

    # --- 传统值 ---
    legacy_on = _reg_value(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS,
                           "ProxyEnable")
    out["legacy_enabled"] = (legacy_on == 1)
    out["legacy_server"] = _reg_value(
        winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, "ProxyServer") or ""
    out["legacy_bypass"] = _reg_value(
        winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, "ProxyOverride") or ""
    out["legacy_pac"] = _reg_value(
        winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS, "AutoConfigURL") or ""

    # --- 二进制块 ---
    blob = _reg_bytes(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY,
                      "DefaultConnectionSettings")
    parsed = _parse_wininet_blob(blob) if blob else None
    if parsed is None:
        # 有些系统把当前连接的设置放在 Connections\<数字> 子键下
        blob = _reg_bytes(winreg.HKEY_CURRENT_USER, CONNECTIONS_KEY + r"\0",
                          "DefaultConnectionSettings")
        parsed = _parse_wininet_blob(blob) if blob else None

    if parsed is not None:
        out["binary_parsed"] = True
        out["binary_flags"] = parsed["flags"]
        out["binary_server"] = parsed.get("proxy_server", "")
        out["binary_bypass"] = parsed.get("bypass", "")
        out["binary_pac"] = parsed.get("pac_url", "")
        out["binary_enabled"] = bool(parsed["flags"] & FLAG_PROXY)

    # --- 对拍 ---
    le, be = out["legacy_enabled"], out["binary_enabled"]
    ls, bs = out["legacy_server"], out["binary_server"]
    if out["binary_parsed"]:
        if le and not be:
            out["conflict"] = "legacy_only"
        elif be and not le:
            out["conflict"] = "binary_only"
        elif le and be and ls and bs and (ls.strip() != bs.strip()):
            out["conflict"] = "server_diff"
    return out


# ---------------------------------------------------------------- 环境变量


def read_env_proxy() -> dict:
    """读进程环境变量里的代理设置。

    注意：这里读到的是**当前进程**的变量。有些宿主程序（沙箱、IDE、
    终端增强工具）会给子进程注入代理变量，那不是系统级设置，
    所以调用方应当把它当「另一条线」单独显示，而不是覆盖系统代理结论。
    """
    out = {}
    for name in _ENV_NAMES:
        val = os.environ.get(name)
        if val:
            out[name] = val
    return out


# ---------------------------------------------------------------- 监听端口


def find_listeners(procs=None):
    """在 netstat 结果里找「环回地址上的代理端口监听项」。

    不主动发连接：只读 netstat 已有的监听表，所以对代理软件零干扰。
    返回 ``[{"port": int, "pid": int, "proc": str, "known": bool}]``。
    """
    try:
        conns = connscan.list_connections(include_local=True,
                                          include_listening=True)
    except Exception:  # noqa: BLE001
        return []
    if procs is None:
        try:
            # with_paths=False：只走 tasklist，不触发 PowerShell（省 1~2 秒）
            procs = connscan.list_processes(with_paths=False)
        except Exception:  # noqa: BLE001
            procs = {}

    found = {}
    for c in conns:
        if c.proto != "TCP" or c.state != "LISTENING":
            continue
        if c.net_kind != connscan.NET_LOCAL:
            continue
        if c.local_ip not in ("127.0.0.1", "::1", "0.0.0.0", "::"):
            continue
        port = c.local_port
        known = port in PROXY_PORTS
        if not known:
            # 端口不在常见表里时，再看提供方是不是已知代理进程
            info = procs.get(c.pid)
            pname = (info.name if info else "").lower()
            if pname not in PROXY_APPS:
                continue
        key = (port, c.pid)
        if key in found:
            continue
        info = procs.get(c.pid)
        pname = info.name if info else ""
        found[key] = {
            "port": port,
            "pid": c.pid,
            "proc": pname,
            "known": known,
            "app": PROXY_APPS.get(pname.lower(), ""),
        }
    return sorted(found.values(), key=lambda d: d["port"])


def running_proxy_apps(procs=None) -> list:
    """列出正在运行的代理客户端（不要求它一定在监听）。

    有些客户端只在系统代理开启时才起内核，内核进程名未必在监听表里，
    所以这里单独按进程名扫一遍，避免漏报「本机有代理软件」。
    """
    if procs is None:
        try:
            procs = connscan.list_processes(with_paths=False)
        except Exception:  # noqa: BLE001
            procs = {}
    names = []
    for info in procs.values():
        app = PROXY_APPS.get((info.name or "").lower())
        if app and app not in names:
            names.append(app)
    return names


# ---------------------------------------------------------------- 统一结论


@dataclass
class ProxyInfo:
    """本机代理的整体情况。字段都填成「可直接显示」的形式。"""

    enabled: bool = False          # 结论：本机是否在走代理
    source: str = ""               # binary / legacy / pac / env / process
    endpoint: str = ""             # 形如 127.0.0.1:7890
    port: int = 0
    server: str = ""               # 生效那份的原始串
    bypass: str = ""
    pac: str = ""
    legacy_enabled: bool = False
    legacy_server: str = ""
    binary_enabled: bool = False
    binary_server: str = ""
    binary_flags: int = 0
    binary_parsed: bool = False
    conflict: str = ""             # 见 read_wininet()
    env: dict = field(default_factory=dict)
    listeners: list = field(default_factory=list)
    apps: list = field(default_factory=list)
    ports_open: list = field(default_factory=list)
    listening: bool = False        # 代理端口是否真的在监听
    error: str = ""

    # -- 显示辅助 ---------------------------------------------------

    @property
    def app_label(self) -> str:
        """在跑的代理客户端名字。

        优先取「真正在监听代理端口的那个」——比按进程名扫出来的更准：
        装了 Clash Verge 的机器上，服务进程（clash-verge-service）也会被扫到，
        但它只是个托管服务，把流量送出去的是内核（mihomo）。
        """
        for item in self.listeners:
            if item.get("app"):
                return item["app"]
        if self.apps:
            return " / ".join(self.apps[:2])
        return ""

    @property
    def who(self) -> str:
        """「谁在提供这个代理」。没有就返回空串。"""
        label = self.app_label
        if label:
            return label
        for item in self.listeners:
            if item.get("proc"):
                return item["proc"]
        return ""

    @property
    def conflict_text(self) -> str:
        return {
            "legacy_only": "注册表两处不一致：浏览器实际在直连",
            "binary_only": "注册表两处不一致：浏览器在用代理",
            "server_diff": "注册表两处不一致：代理地址不同",
        }.get(self.conflict, "")


def _split_hostport(raw: str):
    """从 'http=127.0.0.1:7890;https=...' 或 '127.0.0.1:7890' 取第一组 host:port。

    返回 ``(hostport, port)``，取不到就是 ``("", 0)``。
    """
    if not raw:
        return "", 0
    for part in str(raw).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            _, _, part = part.partition("=")
            part = part.strip()
        if not part:
            continue
        # 有的写 http://127.0.0.1:7890，把协议头去掉
        if "://" in part:
            netloc = urlsplit(part).netloc
            part = netloc or part.split("://", 1)[1]
        host, _, port_s = part.rpartition(":")
        if not host or not port_s.isdigit():
            continue
        return part, int(port_s)
    return "", 0


def detect(with_listeners: bool = True) -> ProxyInfo:
    """主入口：给出一份可直接显示的本机代理结论。

    with_listeners=True 会跑一次 netstat + tasklist（约 0.3~0.5 秒），
    应在工作线程里调用，别放在界面线程。
    """
    pi = ProxyInfo()
    try:
        w = read_wininet()
    except Exception as exc:  # noqa: BLE001
        pi.error = f"{type(exc).__name__}: {exc}"
        w = {"legacy_enabled": False, "legacy_server": "",
             "binary_enabled": False, "binary_server": "",
             "binary_flags": 0, "binary_parsed": False, "conflict": ""}
    pi.legacy_enabled = w["legacy_enabled"]
    pi.legacy_server = w["legacy_server"]
    pi.binary_enabled = w["binary_enabled"]
    pi.binary_server = w["binary_server"]
    pi.binary_flags = w["binary_flags"]
    pi.binary_parsed = w["binary_parsed"]
    pi.conflict = w["conflict"]
    pi.pac = w.get("legacy_pac") or w.get("binary_pac") or ""
    pi.env = read_env_proxy()

    if with_listeners:
        try:
            pi.listeners = find_listeners()
        except Exception:  # noqa: BLE001
            pi.listeners = []
        try:
            pi.apps = running_proxy_apps()
        except Exception:  # noqa: BLE001
            pi.apps = []
        pi.ports_open = sorted({x["port"] for x in pi.listeners})

    # --- 生效结论：以二进制块（浏览器口径）为准 ---
    if pi.binary_parsed:
        if pi.binary_enabled:
            pi.enabled = True
            pi.source = "binary"
            pi.server = pi.binary_server
        elif pi.pac or (pi.binary_flags & FLAG_PAC):
            pi.enabled = True
            pi.source = "pac"
            pi.server = pi.pac
    else:
        if pi.legacy_enabled:
            pi.enabled = True
            pi.source = "legacy"
            pi.server = pi.legacy_server
        elif pi.pac:
            pi.enabled = True
            pi.source = "pac"
            pi.server = pi.pac

    # 系统代理没开，但本机确实有代理在监听 → 仍然算「有代理」，
    # 只是流量没被迫走它（应用自己指定代理时才会用）。
    if not pi.enabled and pi.listeners:
        pi.enabled = True
        pi.source = "process"

    endpoint, port = _split_hostport(pi.server)
    if not endpoint and pi.listeners:
        first = pi.listeners[0]
        endpoint = f"127.0.0.1:{first['port']}"
        port = first["port"]
    pi.endpoint = endpoint
    pi.port = port
    pi.listening = bool(port and port in pi.ports_open)
    if not pi.listening and pi.ports_open and not port:
        pi.listening = True
    return pi


# ---------------------------------------------------------------- 文字输出


def describe(pi: ProxyInfo, short: bool = False) -> str:
    """一句话描述。short=True 时压到约 20 个字，用于状态栏。"""
    if pi.error and not pi.legacy_server and not pi.listeners:
        return f"代理检测失败（{pi.error}）"
    if not pi.enabled and not pi.listeners and not pi.apps:
        return "未检测到代理（直连）"
    if not pi.enabled:
        apps = "、".join(pi.apps[:2])
        if apps:
            return f"有代理软件在运行（{apps}），但系统代理未启用"
        return "有代理端口在监听，但系统代理未启用"

    who = pi.who
    tail = f"（{who}）" if who else ""
    if pi.source == "process":
        ports = "、".join(str(p) for p in pi.ports_open[:2])
        return f"代理软件在运行：{ports or '端口未知'}{tail}"
    if short:
        return pi.endpoint or "已启用"
    if not pi.listening and pi.port:
        return f"{pi.endpoint} 已启用但端口未监听{tail}"
    return f"{pi.endpoint}{tail}"


def summary_lines(pi: ProxyInfo) -> list:
    """多行详情，供报告与诊断明细使用。"""
    lines = []
    if pi.source == "binary":
        lines.append(f"· 系统代理：启用 → {pi.endpoint}（来自连接设置二进制块）")
    elif pi.source == "legacy":
        lines.append(f"· 系统代理：启用 → {pi.endpoint}（来自注册表传统值）")
    elif pi.source == "pac":
        lines.append(f"· 自动配置脚本（PAC）：{pi.pac or '已启用'}")
    elif pi.source == "process":
        lines.append("· 系统代理：未启用；但本机有代理软件在运行")
    else:
        lines.append("· 系统代理：未启用")

    if pi.conflict:
        lines.append(f"· ⚠ {pi.conflict_text}"
                     f"（传统值 {pi.legacy_server or '关'} / "
                     f"二进制块 {pi.binary_server or '关'}）")
    if pi.apps:
        lines.append(f"· 在运行的代理客户端：{'、'.join(pi.apps)}")
    if pi.ports_open:
        lines.append(f"· 环回地址上在监听的代理端口："
                     f"{'、'.join(str(p) for p in pi.ports_open)}")
    elif pi.enabled:
        lines.append("· 环回地址上未发现常见代理端口在监听")
    if pi.env:
        pairs = "，".join(f"{k}={v}" for k, v in pi.env.items()
                          if k.lower() != "no_proxy")
        if pairs:
            lines.append(f"· 进程环境变量代理：{pairs}")
    return lines
