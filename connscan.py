# -*- coding: utf-8 -*-
"""连接枚举引擎：列出本机所有进程的网络连接。

纯逻辑，无界面依赖，可命令行独立运行：

    python connscan.py                   # 只列公网连接（默认）
    python connscan.py --all             # 含局域网
    python connscan.py --all --listen    # 再带上本机监听口（TCP LISTENING / UDP `*:*`）
    python connscan.py --any --listen    # 全部，连环回一起

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
# 本机在监听、没有对端的一类（TCP LISTENING / UDP 的 `*:*`）。
# 单独成一类而不是塞进「本机」：游戏私服、Web 服务这些「我开给别人的口」
# 全在这一类里，按「本机」归类会让人以为是自己连自己。
NET_LISTEN = "listen"

# 「不限范围」的哨兵值，给 collect(min_kind=...) 用
NET_ANY = "any"

NET_LABEL = {
    NET_LOCAL: "本机",
    NET_LAN: "局域网",
    NET_PUBLIC: "公网",
    NET_MULTICAST: "组播",
    NET_UNKNOWN: "未知",
    NET_LISTEN: "监听",
}

# UDP / TCP 里表示「没有对端」的远端写法。netstat 在 UDP 监听行写 `*:*`，
# 偶尔也会把地址写成全零。这些值**不能**当 IP 解析（解析必失败 →
# 落进 UNKNOWN → 被范围过滤掉，于是本机开的服务器端口永远看不见）。
WILDCARD_HOSTS = ("", "*", "0.0.0.0", "::", "0:0:0:0:0:0:0:0")

# ---------------------------------------------------------------- 连接状态

# netstat 的状态写法**有两套**，同一条连接在两边叫法不同：
#   Windows 用下划线：FIN_WAIT_1 / FIN_WAIT_2 / SYN_RECEIVED
#   类 Unix 不用下划线：FIN_WAIT1 / FIN_WAIT2 / SYN_RECV
# 实测本机（中文 Windows）的 FIN_WAIT_2 就带下划线。
# 若只按其中一套写判断条件，另一套会静默漏过去 —— 之前就漏掉了 6 条
# 已进入关闭流程的残留连接（状态列还因为超宽被切成 `FIN_W`，看着像出错）。
# 所以统一在这里归一化成「无下划线」这一套，后面所有判断只认归一值。
_STATE_ALIASES = {
    "FIN_WAIT_1": "FIN_WAIT1",
    "FIN_WAIT_2": "FIN_WAIT2",
    "SYN_RECEIVED": "SYN_RECV",
    "SYN_RECV": "SYN_RECV",
    "LISTEN": "LISTENING",
    "LISTENING": "LISTENING",
}

# 已经走完（或正在走）关闭流程、不再代表「当前正在连的服务器」的状态。
# 留着只会把结果淹掉 —— 实测 46 条记录里一大半是它们。
STALE_STATES = (
    "TIME_WAIT", "CLOSE_WAIT",
    "FIN_WAIT1", "FIN_WAIT2",
    "LAST_ACK", "CLOSING", "DELETE_TCB", "CLOSED",
)


def normalize_state(state: str) -> str:
    """把两套写法归一化到同一套（无下划线那套）。"""
    s = (state or "").strip().upper()
    return _STATE_ALIASES.get(s, s)


def classify_ip(ip: str) -> str:
    """判断一个 IP 属于哪类网络。无法解析时按「本机」处理，避免误报成公网。

    通配地址（UDP 监听行的 `*`）也归「本机」——它不是某个外部对端，
    而是本机自己开着的口。真空值（空串）同理。
    """
    if ip in WILDCARD_HOSTS:
        return NET_LOCAL
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
    # netstat 把「没有对端」的 UDP 行远端写作 `*:*`（主机和端口都是 `*`）。
    # 必须在 rpartition 之前拦掉：否则端口 `*` 转 int 失败，函数会把
    # 整个串 `*:*` 当作主机名返回，后面无论按通配还是按 IP 判断都对不上，
    # 整行静默消失 —— 七日杀的 26900/26902 就是这么丢的。
    if text.startswith("*"):
        return "*", 0
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
    # 这一条是不是「只有本机在听、没有对端」的项（TCP LISTENING / UDP `*:*`）。
    # 这类行的 remote_ip/remote_port 是空值，展示与排序都必须改用本地地址，
    # 否则表格里会显示成「服务器 IP：0.0.0.0、端口：0」。
    listen_only: bool = False

    @property
    def has_remote(self) -> bool:
        """有没有真实的远端。空 / 通配都不算。"""
        return (self.remote_ip not in WILDCARD_HOSTS) and self.remote_port != 0

    @property
    def is_listen_only(self) -> bool:
        return (self.listen_only or self.state == "LISTENING"
                or (self.proto == "UDP" and not self.has_remote))

    @property
    def display_ip(self) -> str:
        """表格「服务器 IP」列要显示的值。

        监听项显示本机监听地址（`0.0.0.0` = 所有网卡），
        这样「谁在哪个口上等连接」一眼能看出，而不是一行空白。
        """
        if self.is_listen_only:
            return self.local_ip or self.remote_ip
        return self.remote_ip

    @property
    def display_port(self) -> int:
        """表格「端口」列要显示的值。监听项看本地端口。"""
        if self.is_listen_only:
            return self.local_port
        return self.remote_port

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
        return self.state in ("ESTABLISHED", "SYN_SENT", "SYN_RECV",
                             "CLOSE_WAIT")

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
    8081: "HTTP 备用端口 / 控制台",
    8443: "HTTPS 备用端口",
    8888: "HTTP 备用端口",
    9000: "服务端口",
    11211: "Memcached 缓存",
    26900: "七日杀 游戏端口",
    26901: "七日杀 Steam 查询",
    26902: "七日杀 查询 / RCON",
    26903: "七日杀 Web API",
    27015: "Steam / Source 查询",
    27017: "MongoDB 数据库",
    27020: "Steam 游戏端口",
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
    "7daystodie.exe": "七日杀（主机 / 客户端）",
    "7daystodieserver.exe": "七日杀 专用服务端",
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
    "7daystodie.exe": "游戏服务端",
    "7daystodieserver.exe": "游戏服务端",
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
            # 归一化状态写法（Windows 的 FIN_WAIT_2 → FIN_WAIT2 等）
            state = normalize_state(state)

        try:
            pid = int(pid_s)
        except ValueError:
            continue

        l_ip, l_port = split_hostport(local)
        r_ip, r_port = split_hostport(remote)

        # 「只在听、没有对端」的两类：
        #   TCP  LISTENING  —— 状态本身就是监听
        #   UDP  `*:*`      —— UDP 没有状态列，远端写作通配
        wild_remote = (r_ip in WILDCARD_HOSTS) and r_port == 0
        listen_only = (state == "LISTENING") or (proto == "UDP" and wild_remote)

        # 监听项单独归一类。若交给 classify_ip(r_ip)，`*` 会落进「本机」，
        # 于是本机开的服务器端口非得切到「含本机」才看得见 —— 可它明明
        # 是「我开给局域网/外网的口」，该跟局域网档一起出现。
        kind = NET_LISTEN if listen_only else classify_ip(r_ip)

        if not include_local and kind not in (NET_PUBLIC,):
            continue
        # include_listening 判断的是「有没有对端」，不是「状态叫什么」。
        # 只认 state == "LISTENING" 会漏掉全部 UDP 监听行 —— 七日杀的
        # 26900/26902 恰恰是 UDP 的，这就是「端口开着却搜不到」的直接原因。
        if not include_listening and listen_only:
            continue

        conns.append(Connection(
            proto=proto,
            local_ip=l_ip, local_port=l_port,
            remote_ip=r_ip, remote_port=r_port,
            state=state, pid=pid, net_kind=kind,
            # 监听项的「端口语义」要看本地口：本地 26900 才该提示「七日杀 游戏端口」
            service=port_hint(l_port if listen_only else r_port),
            listen_only=listen_only,
        ))

    return conns


def collect(include_local: bool = False, include_listening: bool = False,
            min_kind: str = NET_PUBLIC, include_stale: bool = False,
            include_system: bool = False) -> list:
    """主入口：枚举连接并补上进程信息。

    min_kind 决定「范围」：
      NET_PUBLIC —— 只有公网（默认视图）
      NET_LAN    —— 公网 + 局域网 + 监听口（本机开给别人的服务都在这）
      NET_ANY    —— 不限，连环回（127.0.0.1）与未知分类一起收

    include_listening=True 才会带上「只在听、没有对端」的项
    （TCP LISTENING / UDP `*:*`）。默认关掉是因为它们不是「连到哪去了」，
    但要查「本机服务器的端口有没有起来」就必须打开。

    include_stale=False 会丢掉 TIME_WAIT / CLOSE_WAIT / FIN_WAIT_1 / FIN_WAIT_2
    这类已经结束、还挂在表里的残留项——它们不是「当前正在连接」的服务器，
    留着会把结果淹掉（实测 46 条里有一大半是它）。
    include_system=False 会丢掉 PID 0/4 的内核连接表项。
    """
    if min_kind in (NET_ANY, "all"):
        allow = None                       # 不限范围，环回与未知一起收
    elif min_kind == NET_LAN:
        allow = {NET_PUBLIC, NET_LAN, NET_LISTEN}
    else:
        allow = {NET_PUBLIC}
    procs = list_processes()
    out = []
    for c in list_connections(include_local=True, include_listening=include_listening):
        if allow is not None and c.net_kind not in allow:
            continue
        if not include_stale and c.state in STALE_STATES:
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
    """命令行：
        python connscan.py                  只列公网（默认）
        python connscan.py --all            含局域网
        python connscan.py --all --listen   再带上本机监听口（查 26900 用这个）
        python connscan.py --any --listen   全部，连环回一起
    """
    if "--any" in argv:
        scope = NET_ANY
    elif "--all" in argv:
        scope = NET_LAN
    else:
        scope = NET_PUBLIC
    conns = collect(min_kind=scope, include_listening="--listen" in argv)
    procs = {}
    for c in conns:
        procs.setdefault((c.proc_name, c.pid), []).append(c)

    print(f"共 {len(conns)} 条连接，涉及 {len(procs)} 个进程")
    for (name, pid), items in sorted(procs.items(), key=lambda kv: -len(kv[1])):
        print(f"\n[{pid}] {human_proc_name(name)}  ({len(items)} 条)")
        for c in sorted(items, key=lambda x: (x.display_ip, x.display_port)):
            mark = "监听" if c.is_listen_only else c.state
            tag = c.service or ""
            print(f"    {c.display_ip}:{c.display_port:<16} {mark:<12} {tag}")


if __name__ == "__main__":
    _main(sys.argv[1:])
