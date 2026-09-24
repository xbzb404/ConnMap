# -*- coding: utf-8 -*-
"""连接枚举引擎：列出本机所有进程的网络连接。

纯逻辑，无界面依赖，可命令行独立运行：

    python connscan.py            # 列出全部连接（含本地）
    python connscan.py --public   # 只列公网连接

数据来源是系统自带的 netstat / tasklist，不依赖 psutil 或第三方库。
两个命令的输出都是 GBK 编码，必须按 GBK 解码，否则中文进程名会乱码。

进程的**可执行文件完整路径**另走 PowerShell（Get-CimInstance）。
wmic.exe 已弃用且被本机安全策略禁止调用，只能走这条路。
路径用于「打开文件所在位置」和取进程图标，拿不到也不影响连接枚举。
"""
from __future__ import annotations

import csv
import io
import ipaddress
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 网络类型

NET_LOCAL = "local"        # 环回 / 本机自身
NET_LAN = "lan"            # 局域网、私网
NET_PUBLIC = "public"      # 公网
NET_MULTICAST = "multicast"  # 组播 / 广播
NET_UNKNOWN = "unknown"

NET_LABEL = {
    NET_LOCAL: "本机",
    NET_LAN: "局域网",
    NET_PUBLIC: "公网",
    NET_MULTICAST: "组播",
    NET_UNKNOWN: "未知",
}


def classify_ip(ip: str) -> str:
    """判断一个 IP 属于哪类网络。无法解析时按「本机」处理，避免误报成公网。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return NET_UNKNOWN
    if addr.is_loopback:
        return NET_LOCAL
    if addr.is_multicast:
        return NET_MULTICAST
    if addr.is_link_local:
        return NET_LAN
    if addr.is_unspecified:
        return NET_LOCAL
    # 私有网段（10./172.16-31./192.168./fc00::）
    if addr.is_private:
        return NET_LAN
    if addr.is_reserved:
        return NET_UNKNOWN
    return NET_PUBLIC


def split_hostport(text: str):
    """拆分 `IP:端口`，兼容 IPv6 的 `[::1]:443` 写法。"""
    text = (text or "").strip()
    if not text:
        return "", 0
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            host = text[1:end]
            rest = text[end + 1:]
            port = 0
            if rest.startswith(":"):
                try:
                    port = int(rest[1:])
                except ValueError:
                    port = 0
            return host, port
    if ":" in text:
        host, _, p = text.rpartition(":")
        try:
            return host, int(p)
        except ValueError:
            return text, 0
    return text, 0


# ---------------------------------------------------------------- 数据结构


@dataclass
class Connection:
    """一条连接。"""

    proto: str                 # TCP / UDP
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    state: str                 # ESTABLISHED / LISTENING / ...
    pid: int
    proc_name: str = ""
    proc_path: str = ""
    net_kind: str = NET_UNKNOWN
    # 地理位置，由 geoloc 模块填充
    geo: dict = field(default_factory=dict)
    # 服务/端口语义
    service: str = ""
    port_hint: str = ""

    @property
    def remote_hostport(self) -> str:
        if ":" in self.remote_ip:
            return f"[{self.remote_ip}]:{self.remote_port}"
        return f"{self.remote_ip}:{self.remote_port}"

    @property
    def local_hostport(self) -> str:
        if ":" in self.local_ip:
            return f"[{self.local_ip}]:{self.local_port}"
        return f"{self.local_ip}:{self.local_port}"

    @property
    def uid(self) -> str:
        """连接的唯一标识，用于去重与缓存键。"""
        return f"{self.proto}|{self.local_hostport}|{self.remote_hostport}|{self.pid}"

    @property
    def is_active(self) -> bool:
        return self.state in ("ESTABLISHED", "SYN_SENT", "SYN_RECV", "CLOSE_WAIT")

    @property
    def is_listening(self) -> bool:
        return self.state == "LISTENING"


@dataclass
class ProcessInfo:
    pid: int
    name: str
    path: str = ""
    company: str = ""
    desc: str = ""


# ------------------------------------------------- 常见端口 / 服务的语义提示

# 端口 → 大致用途。只用于给人看的提示，不参与判定逻辑。
PORT_HINTS = {
    22: "SSH 远程管理",
    25: "SMTP 邮件发送",
    53: "DNS 域名解析",
    80: "HTTP 网页",
    110: "POP3 邮件收取",
    123: "NTP 时间同步",
    143: "IMAP 邮件收取",
    443: "HTTPS 加密网页",
    445: "SMB 文件共享",
    465: "SMTPS 邮件发送",
    587: "SMTP 邮件提交",
    853: "DNS over TLS",
    993: "IMAPS 邮件收取",
    995: "POP3S 邮件收取",
    1080: "SOCKS 代理",
    1194: "OpenVPN",
    1433: "SQL Server 数据库",
    1521: "Oracle 数据库",
    3306: "MySQL 数据库",
    3389: "远程桌面",
    5432: "PostgreSQL 数据库",
    5672: "RabbitMQ 消息队列",
    6379: "Redis 缓存",
    7890: "Clash 代理口",
    7891: "Clash 代理口",
    8080: "HTTP 备用端口",
    8443: "HTTPS 备用端口",
    8888: "HTTP 备用端口",
    9000: "服务端口",
    11211: "Memcached 缓存",
    27017: "MongoDB 数据库",
}

# 进程名 → 人话名称。命中就显示更友好的名字。
PROC_ALIASES = {
    "msedge.exe": "Microsoft Edge 浏览器",
    "chrome.exe": "Google Chrome 浏览器",
    "firefox.exe": "Firefox 浏览器",
    "iexplore.exe": "Internet Explorer",
    "qq.exe": "QQ",
    "wechat.exe": "微信",
    "weixin.exe": "微信",
    "dingtalk.exe": "钉钉",
    "telegram.exe": "Telegram",
    "discord.exe": "Discord",
    "steam.exe": "Steam",
    "u3ds.exe": "Unturned 服务端",
    "steamwebhelper.exe": "Steam 内置浏览器",
    "svchost.exe": "Windows 系统服务宿主",
    "system": "Windows 系统内核",
    "system idle process": "系统空闲进程",
    "lsass.exe": "Windows 安全认证",
    "services.exe": "Windows 服务控制",
    "explorer.exe": "Windows 资源管理器",
    "searchhost.exe": "Windows 搜索",
    "runtimebroker.exe": "Windows 应用代理",
    "dllhost.exe": "Windows COM 代理",
    "nvcontainer.exe": "NVIDIA 显卡服务",
    "nvidia web helper.exe": "NVIDIA 服务",
    "onedrive.exe": "OneDrive 云盘",
    "dropbox.exe": "Dropbox 云盘",
    "outlook.exe": "Outlook 邮件",
    "code.exe": "VS Code 编辑器",
    "pycharm64.exe": "PyCharm",
    "node.exe": "Node.js",
    "python.exe": "Python",
    "java.exe": "Java",
    "git.exe": "Git",
    "workbuddy.exe": "WorkBuddy",
    "mstsc.exe": "远程桌面客户端",
    "curl.exe": "curl 命令行",
    "powershell.exe": "PowerShell",
    "cmd.exe": "命令提示符",
    "windowsapps\\python.exe": "Python",
    "mspcmanager.exe": "微软电脑管家",
    "mspcmanagerservice.exe": "微软电脑管家服务",
    "nextin.mihomohost.exe": "Mihomo 代理内核",
    "clash.exe": "Clash 代理",
    "clash-verge.exe": "Clash Verge",
    "v2rayn.exe": "v2rayN 代理",
    "xray.exe": "Xray 代理内核",
    "sing-box.exe": "sing-box 代理内核",
    "wemeetapp.exe": "腾讯会议",
    "wechatweb.exe": "微信",
    "tim.exe": "TIM",
    "feishu.exe": "飞书",
    "lark.exe": "飞书",
    "baidunetdisk.exe": "百度网盘",
    "aliyundrive.exe": "阿里云盘",
    "thunder.exe": "迅雷",
    "qbittorrent.exe": "qBittorrent",
    "transmission.exe": "Transmission",
    "vlc.exe": "VLC 播放器",
    "spotify.exe": "Spotify",
    "netease music.exe": "网易云音乐",
    "cloudmusic.exe": "网易云音乐",
    "qqmusic.exe": "QQ 音乐",
    "wps.exe": "WPS Office",
    "et.exe": "WPS 表格",
    "wpp.exe": "WPS 演示",
    "winword.exe": "Word",
    "excel.exe": "Excel",
    "powerpnt.exe": "PowerPoint",
    "notepad.exe": "记事本",
    "everything.exe": "Everything 搜索",
    "360tray.exe": "360 安全卫士",
    "huorong.exe": "火绒安全",
    "hipsmain.exe": "火绒安全",
    "kxescore.exe": "金山毒霸",
    "qqpcmgr.exe": "腾讯电脑管家",
    "browser.exe": "360 浏览器",
    "2345explorer.exe": "2345 浏览器",
    "sogouexplorer.exe": "搜狗浏览器",
    "typora.exe": "Typora",
    "obs64.exe": "OBS 录屏",
    "adb.exe": "Android 调试桥",
    "navicat.exe": "Navicat",
    "xshell.exe": "Xshell",
    "xftp.exe": "Xftp",
    "putty.exe": "PuTTY",
    "mobaxterm.exe": "MobaXterm",
    "wireshark.exe": "Wireshark",
    "fiddler.exe": "Fiddler",
    "ida64.exe": "IDA Pro",
}

# 进程名 → 归类。用于「按应用」汇总时把系统组件收拢。
PROC_CATEGORY = {
    "system": "系统核心",
    "system idle process": "系统核心",
    "registry": "系统核心",
    "smss.exe": "系统核心",
    "csrss.exe": "系统核心",
    "wininit.exe": "系统核心",
    "winlogon.exe": "系统核心",
    "services.exe": "系统服务",
    "lsass.exe": "系统服务",
    "svchost.exe": "系统服务",
    "dllhost.exe": "系统服务",
    "runtimebroker.exe": "系统服务",
    "searchhost.exe": "系统服务",
    "sihost.exe": "系统服务",
    "taskhostw.exe": "系统服务",
    "explorer.exe": "系统外壳",
    "conhost.exe": "系统服务",
    "spoolsv.exe": "系统服务",
    "audiodg.exe": "系统服务",
    "fontdrvhost.exe": "系统服务",
    "wmiprvse.exe": "系统服务",
    "msmpeng.exe": "Windows 安全中心",
    "nissrv.exe": "Windows 安全中心",
    "securityhealthservice.exe": "Windows 安全中心",
    "sgrmbroker.exe": "Windows 安全中心",
    "mspcmanagerservice.exe": "系统服务",
    "msedge.exe": "浏览器",
    "chrome.exe": "浏览器",
    "firefox.exe": "浏览器",
    "iexplore.exe": "浏览器",
    "qq.exe": "即时通讯",
    "wechat.exe": "即时通讯",
    "weixin.exe": "即时通讯",
    "tim.exe": "即时通讯",
    "dingtalk.exe": "即时通讯",
    "telegram.exe": "即时通讯",
    "discord.exe": "即时通讯",
    "feishu.exe": "即时通讯",
    "lark.exe": "即时通讯",
    "steam.exe": "游戏平台",
    "u3ds.exe": "游戏服务端",
    "steamwebhelper.exe": "游戏平台",
    "clash.exe": "代理工具",
    "clash-verge.exe": "代理工具",
    "v2rayn.exe": "代理工具",
    "xray.exe": "代理工具",
    "sing-box.exe": "代理工具",
    "nextin.mihomohost.exe": "代理工具",
    "nvcontainer.exe": "驱动与硬件",
    "nvidia web helper.exe": "驱动与硬件",
}


def human_proc_name(name: str) -> str:
    """把进程文件名换成更容易读懂的名字。"""
    if not name:
        return "未知进程"
    key = name.strip().lower()
    if key in PROC_ALIASES:
        return PROC_ALIASES[key]
    # 去掉 .exe 结尾后二次匹配
    if key.endswith(".exe"):
        short = key[:-4]
        if short in PROC_ALIASES:
            return PROC_ALIASES[short]
    return name


def proc_category(name: str) -> str:
    if not name:
        return "未知"
    key = name.strip().lower()
    if key in PROC_CATEGORY:
        return PROC_CATEGORY[key]
    if key.endswith(".exe"):
        short = key[:-4]
        if short in PROC_CATEGORY:
            return PROC_CATEGORY[short]
    if "svchost" in key or key.startswith(("win", "ms")):
        return "系统服务"
    return "应用软件"


def port_hint(port: int) -> str:
    return PORT_HINTS.get(port, "")


# ---------------------------------------------------------------- 数据采集


def _run(cmd, timeout=60):
    """执行命令并解码。netstat / tasklist 都是 GBK，失败再退 utf-8。"""
    try:
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    raw = proc.stdout or b""
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _run_text(cmd, timeout=60):
    """执行并解码，返回 (stdout, stderr)。PowerShell 输出可能是 UTF-16/GBK。"""
    try:
        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags = subprocess.CREATE_NO_WINDOW
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    return _decode(proc.stdout or b""), _decode(proc.stderr or b"")


def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    # PowerShell 直出可能是 UTF-16LE（BOM 或 NUL 密集）
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if raw.count(b"\x00") > len(raw) // 8:
        try:
            return raw.decode("utf-16-le", "ignore")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


def list_process_paths(timeout=45) -> dict:
    """取 PID → 可执行文件完整路径。

    走 PowerShell 的 Win32_Process.ExecutablePath。
    取不到（权限不足、进程已退出）就返回空表，调用方按「路径未知」处理。
    """
    # 用「|」分隔 pid 与路径，避免路径里的空格干扰解析
    script = ("Get-CimInstance Win32_Process | "
              "Where-Object { $_.ExecutablePath } | "
              "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }")
    out, _err = _run_text(_PS + [script], timeout=timeout)
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid_s, _, path = line.partition("|")
        pid_s, path = pid_s.strip(), path.strip()
        if not path:
            continue
        try:
            result[int(pid_s)] = path
        except ValueError:
            continue
    return result


def list_processes(with_paths: bool = True) -> dict:
    """取 PID → 进程信息的映射。

    名字走 tasklist（系统自带、编码稳定）；路径走 PowerShell。
    两条路都失败也不影响连接枚举，只是少一列信息。
    """
    text = _run(["tasklist", "/fo", "csv", "/nh"])
    result = {}
    if text:
        for row in csv.reader(io.StringIO(text)):
            if len(row) >= 2:
                name, pid_s = row[0].strip(), row[1].strip()
                try:
                    pid = int(pid_s)
                except ValueError:
                    continue
                result[pid] = ProcessInfo(pid, name)

    if with_paths and result:
        paths = list_process_paths()
        for pid, info in result.items():
            p = paths.get(pid)
            if p:
                info.path = p
    return result


def list_connections(include_local: bool = True, include_listening: bool = False) -> list:
    """枚举本机连接。

    include_local=False 时只保留公网连接（最常用的视图）。
    include_listening=False 时丢掉 LISTENING，因为那不是「正在连接的对方」。
    """
    text = _run(["netstat", "-ano"])
    if not text:
        return []

    conns = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0].upper()
        if proto not in ("TCP", "UDP"):
            continue
        # UDP 行没有状态列：UDP  local  remote  pid
        if proto == "UDP":
            if len(parts) < 4:
                continue
            local, remote, pid_s = parts[1], parts[2], parts[3]
            state = "UDP"
        else:
            if len(parts) < 5:
                # TCP 少数情况缺状态，跳过
                continue
            local, remote, state, pid_s = parts[1], parts[2], parts[3], parts[4]

        try:
            pid = int(pid_s)
        except ValueError:
            continue

        l_ip, l_port = split_hostport(local)
        r_ip, r_port = split_hostport(remote)
        kind = classify_ip(r_ip)

        if not include_local and kind not in (NET_PUBLIC,):
            continue
        if not include_listening and state == "LISTENING":
            continue

        conns.append(Connection(
            proto=proto,
            local_ip=l_ip, local_port=l_port,
            remote_ip=r_ip, remote_port=r_port,
            state=state, pid=pid, net_kind=kind,
            service=port_hint(r_port),
        ))

    return conns


def collect(include_local: bool = False, include_listening: bool = False,
            min_kind: str = NET_PUBLIC, include_stale: bool = False,
            include_system: bool = False) -> list:
    """主入口：枚举连接并补上进程信息。

    min_kind=NET_PUBLIC 时只返回公网连接；=NET_LAN 时连局域网一起返回。

    include_stale=False 会丢掉 TIME_WAIT / CLOSE_WAIT 这类已经结束、
    还挂在表里的残留项——它们不是「当前正在连接」的服务器，留着会把
    结果淹掉（实测 46 条里有一大半是它）。
    include_system=False 会丢掉 PID 0/4 的内核连接表项。
    """
    allow = {NET_PUBLIC} if min_kind == NET_PUBLIC else {NET_PUBLIC, NET_LAN}
    procs = list_processes()
    out = []
    for c in list_connections(include_local=True, include_listening=include_listening):
        if c.net_kind not in allow:
            continue
        if not include_stale and c.state in ("TIME_WAIT", "CLOSE_WAIT",
                                             "FIN_WAIT1", "FIN_WAIT2",
                                             "LAST_ACK", "CLOSING", "TIME_WAIT"):
            continue
        # PID 0 是内核空闲进程，netstat 把系统级连接表项挂在它下面；
        # PID 4 是内核本身。它们不对应任何用户应用。
        if not include_system and c.pid in (0, 4):
            continue
        info = procs.get(c.pid)
        if info is not None:
            c.proc_name = info.name
            c.proc_path = info.path
        out.append(c)
    return out


# ---------------------------------------------------------------- 汇总统计


def public_ip_set(conns) -> list:
    """取出需要查地理位置的公网 IP（去重）。"""
    seen = {}
    for c in conns:
        if c.net_kind == NET_PUBLIC and c.remote_ip not in seen:
            seen[c.remote_ip] = True
    return list(seen.keys())


def group_by_process(conns) -> dict:
    """按进程聚合：进程名 → 连接列表。"""
    groups = {}
    for c in conns:
        key = (c.proc_name, c.pid)
        groups.setdefault(key, []).append(c)
    return groups


def group_by_region(conns) -> dict:
    """按「国家/地区 + 城市」聚合。"""
    groups = {}
    for c in conns:
        geo = c.geo or {}
        if not geo:
            key = ("未知", "")
        else:
            key = (geo.get("country") or "未知", geo.get("city") or "")
        groups.setdefault(key, []).append(c)
    return groups


def group_by_country(conns) -> dict:
    """按国家/地区聚合，用于图表。"""
    groups = {}
    for c in conns:
        geo = c.geo or {}
        country = geo.get("country") or "未知位置"
        groups.setdefault(country, []).append(c)
    return groups


def summarize(conns) -> dict:
    """整体概览数字。"""
    total = len(conns)
    ips = {c.remote_ip for c in conns}
    procs = {(c.proc_name, c.pid) for c in conns}
    countries = {}
    for c in conns:
        geo = c.geo or {}
        if geo.get("country"):
            countries[geo["country"]] = countries.get(geo["country"], 0) + 1
    return {
        "connections": total,
        "apps": len(procs),
        "remote_ips": len(ips),
        "countries": len(countries),
        "country_list": sorted(countries.items(), key=lambda kv: -kv[1]),
    }


# ---------------------------------------------------------------- 命令行


def _main(argv):
    public_only = "--all" not in argv
    conns = collect(min_kind=NET_PUBLIC if public_only else NET_LAN,
                    include_listening=False)
    procs = {}
    for c in conns:
        procs.setdefault((c.proc_name, c.pid), []).append(c)

    print(f"共 {len(conns)} 条连接，涉及 {len(procs)} 个进程")
    for (name, pid), items in sorted(procs.items(), key=lambda kv: -len(kv[1])):
        print(f"\n[{pid}] {human_proc_name(name)}  ({len(items)} 条)")
        for c in sorted(items, key=lambda x: x.remote_ip):
            tag = c.service or ""
            print(f"    {c.remote_hostport:<26} {c.state:<12} {tag}")


if __name__ == "__main__":
    _main(sys.argv[1:])
