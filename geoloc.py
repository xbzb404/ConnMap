# -*- coding: utf-8 -*-
"""IP 地理位置与机房查询。

纯逻辑，无界面依赖，可命令行独立运行：

    python geoloc.py 8.8.8.8 1.1.1.1
    python geoloc.py --batch ips.txt

设计要点（都是实测踩出来的）：

1. **多库回退链。** 没有哪个免费库是可靠的：ip-api.com 免费档限 45 次/分钟，
   而且本机网络对部分 IP（如 8.8.8.8）走 http 会直接超时；淘宝、美团的公开接口
   一个限流一个无数据。所以按序尝试多个库，第一个成功的就用。

2. **磁盘缓存。** IP 地理位置基本不变，缓存持久化到本地 JSON，
   再查同一台服务器不会重复请求，也不会被限流。

3. **地区名合规化。** 各库返回的台湾/香港/澳门写法五花八门（有的写「中华民国」、
   有的写「Hong Kong SAR China」），统一规范成「中国台湾 / 中国香港 / 中国澳门」。

4. **限流保护。** 主动控制请求节奏，避免触发 45 次/分钟的上限后被整片拒绝。
"""
from __future__ import annotations

import ipaddress
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(HERE, "geo_cache.json")

# 缓存有效期：地理位置变化很慢，给 30 天
CACHE_TTL = 30 * 24 * 3600

# 缓存结构版本。改动识别逻辑（如机房/CDN 判定规则）后必须 +1，
# 否则旧结果会一直命中缓存，看起来像「修了没用」。
CACHE_VERSION = 4

# 请求间隔（秒）。ip-api 免费档 45 次/分钟，留足余量。
MIN_INTERVAL = 0.35

# 单次 HTTP 请求超时
HTTP_TIMEOUT = 10

_UA = "ConnMap/1.0 (+local connection geo lookup)"

# 地区名规范：把各库的各种写法统一成中国大陆口径
REGION_ALIASES = {
    # 台湾
    "台湾": "中国台湾", "台灣": "中国台湾", "taiwan": "中国台湾",
    "taiwan, province of china": "中国台湾", "中华民国": "中国台湾",
    "中華民國": "中国台湾", "taiwan province": "中国台湾",
    "republic of china": "中国台湾", "中国台湾省": "中国台湾",
    # 香港
    "香港": "中国香港", "hong kong": "中国香港", "hong kong sar": "中国香港",
    "hong kong sar china": "中国香港", "hongkong": "中国香港",
    "hong kong sar, china": "中国香港",
    # 澳门
    "澳门": "中国澳门", "澳門": "中国澳门", "macao": "中国澳门",
    "macau": "中国澳门", "macao sar china": "中国澳门",
    "macau sar china": "中国澳门",
}

# 国家名中文化：国际库默认给英文，统一转成中文
COUNTRY_CN = {
    "china": "中国", "united states": "美国", "japan": "日本",
    "singapore": "新加坡", "south korea": "韩国", "korea": "韩国",
    "republic of korea": "韩国", "germany": "德国", "france": "法国",
    "united kingdom": "英国", "netherlands": "荷兰", "russia": "俄罗斯",
    "canada": "加拿大", "australia": "澳大利亚", "india": "印度",
    "brazil": "巴西", "ireland": "爱尔兰", "sweden": "瑞典",
    "switzerland": "瑞士", "italy": "意大利", "spain": "西班牙",
    "poland": "波兰", "finland": "芬兰", "norway": "挪威",
    "denmark": "丹麦", "austria": "奥地利", "belgium": "比利时",
    "vietnam": "越南", "thailand": "泰国", "malaysia": "马来西亚",
    "indonesia": "印度尼西亚", "philippines": "菲律宾", "türkiye": "土耳其",
    "turkey": "土耳其", "israel": "以色列", "united arab emirates": "阿联酋",
    "saudi arabia": "沙特阿拉伯", "south africa": "南非", "egypt": "埃及",
    "mexico": "墨西哥", "argentina": "阿根廷", "chile": "智利",
    "new zealand": "新西兰", "ukraine": "乌克兰", "czechia": "捷克",
    "czech republic": "捷克", "romania": "罗马尼亚", "portugal": "葡萄牙",
    "greece": "希腊", "hungary": "匈牙利", "bulgaria": "保加利亚",
    "lithuania": "立陶宛", "latvia": "拉脱维亚", "estonia": "爱沙尼亚",
    "luxembourg": "卢森堡", "iceland": "冰岛", "croatia": "克罗地亚",
    "slovakia": "斯洛伐克", "slovenia": "斯洛文尼亚", "serbia": "塞尔维亚",
    "kazakhstan": "哈萨克斯坦", "pakistan": "巴基斯坦",
    "bangladesh": "孟加拉国", "sri lanka": "斯里兰卡", "nepal": "尼泊尔",
    "mongolia": "蒙古", "myanmar": "缅甸", "cambodia": "柬埔寨",
    "laos": "老挝", "brunei": "文莱",     "macao": "中国澳门",
    "hong kong": "中国香港", "taiwan": "中国台湾",
}

# 省级行政区中文简称：把各库返回的全称（含省/市/自治区/特别行政区）压成短名。
# 直辖市/特别行政区/自治区都要去后缀，免得「广东省」「北京市」挤占表格宽度。
PROVINCE_CN_FULL = {
    "北京市": "北京", "天津市": "天津", "上海市": "上海", "重庆市": "重庆",
    "河北省": "河北", "山西省": "山西", "辽宁省": "辽宁", "吉林省": "吉林",
    "黑龙江省": "黑龙江", "江苏省": "江苏", "浙江省": "浙江", "安徽省": "安徽",
    "福建省": "福建", "江西省": "江西", "山东省": "山东", "河南省": "河南",
    "湖北省": "湖北", "湖南省": "湖南", "广东省": "广东", "海南省": "海南",
    "四川省": "四川", "贵州省": "贵州", "云南省": "云南", "陕西省": "陕西",
    "甘肃省": "甘肃", "青海省": "青海", "台湾省": "台湾",
    "内蒙古自治区": "内蒙古", "广西壮族自治区": "广西", "西藏自治区": "西藏",
    "宁夏回族自治区": "宁夏", "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港", "澳门特别行政区": "澳门",
}

# 国外州/省英文名 → 中文（常用的一部分；没收录的保留原文）。
PROVINCE_CN_EN = {
    "california": "加利福尼亚", "texas": "得克萨斯", "new york": "纽约州",
    "florida": "佛罗里达", "virginia": "弗吉尼亚", "washington": "华盛顿州",
    "illinois": "伊利诺伊", "massachusetts": "马萨诸塞", "oregon": "俄勒冈",
    "georgia": "佐治亚", "ohio": "俄亥俄", "pennsylvania": "宾夕法尼亚",
    "michigan": "密歇根", "north carolina": "北卡罗来纳",
    "south carolina": "南卡罗来纳", "arizona": "亚利桑那", "colorado": "科罗拉多",
    "minnesota": "明尼苏达", "wisconsin": "威斯康星", "maryland": "马里兰",
    "nevada": "内华达", "utah": "犹他", "missouri": "密苏里", "indiana": "印第安纳",
    "tennessee": "田纳西", "tokyo": "东京", "osaka": "大阪", "kanagawa": "神奈川",
    "aichi": "爱知", "kyoto": "京都", "hokkaido": "北海道", "fukuoka": "福冈",
    "england": "英格兰", "scotland": "苏格兰", "wales": "威尔士",
    "bavaria": "巴伐利亚", "moscow": "莫斯科", "paris": "巴黎",
    "île-de-france": "法兰西岛", "ontario": "安大略",
    "british columbia": "不列颠哥伦比亚", "queensland": "昆士兰",
    "new south wales": "新南威尔士", "victoria": "维多利亚",
    "western cape": "西开普", "catalonia": "加泰罗尼亚",
    "north holland": "北荷兰", "south holland": "南荷兰",
    "lombardy": "伦巴第", "sao paulo": "圣保罗", "dublin": "都柏林",
}

# 繁体 / 异体国名（部分库用繁体返回）统一成简体
TRAD_TO_SIMP = {
    "澳大利亞": "澳大利亚", "澳大利亚": "澳大利亚",
    "紐西蘭": "新西兰", "新西蘭": "新西兰",
    "韓國": "韩国", "俄羅斯": "俄罗斯", "德國": "德国",
    "法國": "法国", "英國": "英国", "荷蘭": "荷兰",
    "加拿大": "加拿大", "美國": "美国", "日本": "日本",
    "新加坡": "新加坡", "馬來西亞": "马来西亚",
    "泰國": "泰国", "越南": "越南", "印度尼西亞": "印度尼西亚",
    "印度": "印度", "巴西": "巴西", "墨西哥": "墨西哥",
    "阿根廷": "阿根廷", "西班牙": "西班牙", "意大利": "意大利",
    "瑞士": "瑞士", "瑞典": "瑞典", "挪威": "挪威",
    "丹麥": "丹麦", "芬蘭": "芬兰", "波蘭": "波兰",
    "愛爾蘭": "爱尔兰", "葡萄牙": "葡萄牙", "希臘": "希腊",
    "土耳其": "土耳其", "以色列": "以色列", "埃及": "埃及",
    "南非": "南非", "智利": "智利", "捷克": "捷克",
    "奧地利": "奥地利", "比利時": "比利时", "冰島": "冰岛",
    "中國": "中国", "中國香港": "中国香港", "中國台灣": "中国台湾",
    "中國澳門": "中国澳门",
}

# 云厂商 / 机房识别：从 org / as 字段里认出是哪家的机房
DATACENTER_PATTERNS = [
    ("腾讯云", ["tencent", "tencent cloud"]),
    ("阿里云", ["alibaba", "aliyun", "alicloud", "hangzhou alibaba"]),
    ("华为云", ["huawei cloud", "huaweicloud", "huawei technologies"]),
    ("百度云", ["baidu"]),
    ("金山云", ["kingsoft", "ksyun"]),
    ("字节跳动", ["bytedance", "volces", "volcengine"]),
    ("微软云 Azure", ["microsoft azure", "azure"]),
    ("AWS", ["amazon", "aws", "amazonaws"]),
    ("谷歌云 GCP", ["google cloud", "google llc", "googleusercontent"]),
    ("Oracle 云", ["oracle"]),
    ("IBM 云", ["ibm", "softlayer"]),
    ("Cloudflare", ["cloudflare"]),
    ("Akamai", ["akamai"]),
    ("Fastly", ["fastly"]),
    ("DigitalOcean", ["digitalocean"]),
    ("Vultr", ["vultr", "choopa", "the constant company"]),
    ("Linode / Akamai", ["linode"]),
    ("Hetzner", ["hetzner"]),
    ("OVH", ["ovh"]),
    ("日本 Sakura", ["sakura internet"]),
    ("中国电信", ["china telecom", "chinatelecom", "chinanet"]),
    ("中国联通", ["china unicom", "chinaunicom"]),
    ("中国移动", ["china mobile", "chinamobile"]),
    ("中国教育网", ["cernet", "china education"]),
    ("广电/歌华", ["china broadcasting", "gehua"]),
]

# 云厂商 → 判定为「数据中心/云机房」而非「家用宽带」
CLOUD_KEYWORDS = [
    "cloud", "azure", "aws", "amazon", "google", "oracle", "ibm",
    "digitalocean", "vultr", "linode", "hetzner", "ovh", "cloudflare",
    "akamai", "fastly", "alibaba", "aliyun", "tencent", "huawei",
    "bytedance", "volcengine", "choopa", "m247", "datacamp",
]

# 机房名里的国家/城市线索，用于把 org 里的地名抠出来
DC_PLACE_HINTS = [
    "southeastasia", "eastasia", "northeastasia", "japaneast", "japanwest",
    "koreacentral", "centralindia", "southindia", "australiaeast",
    "westeurope", "northeurope", "eastus", "westus", "centralus",
    "us-east", "us-west", "eu-west", "eu-central", "ap-northeast",
    "ap-southeast", "ap-south", "ap-east", "cn-north", "cn-east",
]

# CDN 识别：必须命中「确实在做内容分发」的特征。
# 注意不能用厂商名裸匹配——Cloudflare / Akamai 既做 CDN 也做别的一堆事，
# 而 Chinanet / Aliyun / GitHub 分别是骨干网、云主机、源站，都不是 CDN。
# 否则会出现「什么都标成 CDN」的假阳性（实测踩过）。
CDN_HINTS = {
    "cloudfront": "AWS CloudFront CDN",
    "akamai": "Akamai CDN",
    "fastly": "Fastly CDN",
    "edgecast": "EdgeCast CDN",
    "llnw": "Limelight CDN",
    "limelight": "Limelight CDN",
    "kudun": "阿里云 CDN",
    "kunlun": "阿里云 CDN",           # 阿里云 CDN 网络名
    "alikunlun": "阿里云 CDN",
    "cdngslb": "阿里云 CDN",
    "wangsu": "网宿 CDN",
    "chinacache": "蓝汛 CDN",
    "china cache": "蓝汛 CDN",
    "lxdns": "蓝汛 CDN",
    "ksyun cdn": "金山云 CDN",
    "tencent cloud cdn": "腾讯云 CDN",
    "baidu cloud cdn": "百度云 CDN",
    "baiduyun": "百度云加速",
    "cloudflare": "Cloudflare CDN",
    "cloudfront.net": "AWS CloudFront CDN",
}

# 这些名字里虽有 CDN 厂商，但实际是把整块网段租给别家做源站/云主机，
# 不标 CDN。命中即从 CDN 排除。
CDN_EXCLUDE = [
    "amazon data services", "amazon technologies", "aws ec2",
    "google cloud", "microsoft corporation", "microsoft azure",
    "alibaba cloud", "aliyun computing", "github",
]


# ---------------------------------------------------------------- 缓存

_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_call = [0.0]


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


_CACHE = _load_cache()


def cache_stats() -> dict:
    now = time.time()
    fresh = sum(1 for v in _CACHE.values()
                if isinstance(v, dict) and v.get("_v") == CACHE_VERSION
                and now - v.get("_ts", 0) < CACHE_TTL)
    stale = 0
    for v in _CACHE.values():
        if isinstance(v, dict) and (v.get("_v") != CACHE_VERSION
                                    or now - v.get("_ts", 0) >= CACHE_TTL):
            stale += 1
    return {"total": len(_CACHE), "fresh": fresh, "stale": stale,
            "version": CACHE_VERSION}


def clear_cache() -> int:
    n = len(_CACHE)
    _CACHE.clear()
    _save_cache(_CACHE)
    return n


def _cache_get(ip: str):
    rec = _CACHE.get(ip)
    if isinstance(rec, dict):
        if rec.get("_v") != CACHE_VERSION:
            return None
        if time.time() - rec.get("_ts", 0) < CACHE_TTL:
            return rec
    return None


def _cache_put(ip: str, data: dict) -> None:
    with _cache_lock:
        rec = dict(data)
        rec["_ts"] = time.time()
        rec["_v"] = CACHE_VERSION
        _CACHE[ip] = rec
        _save_cache(_CACHE)


# ---------------------------------------------------------------- 名字规范化


def _norm_text(value: str) -> str:
    return (value or "").strip().strip(".").lower()


def normalize_country(name: str) -> str:
    """国家/地区名中文化 + 台湾港澳口径统一。"""
    if not name:
        return ""
    raw = (name or "").strip()
    key = _norm_text(raw)
    if key in REGION_ALIASES:
        return REGION_ALIASES[key]
    if key in COUNTRY_CN:
        return COUNTRY_CN[key]
    # 已经含中文的直接处理
    if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        for bad, good in REGION_ALIASES.items():
            if bad in raw:
                return good
        # 繁体转简体
        simp = TRAD_TO_SIMP.get(raw)
        if simp is None:
            simp = _to_simplified(raw)
        # 正式国名「中华人民共和国」在日常展示里统一简称为「中国」
        if simp in ("中华人民共和国", "中国人民共和国"):
            return "中国"
        if simp in TRAD_TO_SIMP:
            return TRAD_TO_SIMP[simp]
        return simp
    # 英文未收录的，保留原文（总比丢掉信息好）
    return raw


# 常见繁体字 → 简体字（国名里高频出现的那些）
_TRAD_CHARS = {
    "國": "国", "華": "华", "臺": "台", "灣": "湾", "島": "岛",
    "韓": "韩", "俄": "俄", "羅": "罗", "斯": "斯", "德": "德",
    "蘭": "兰", "荷": "荷", "澳": "澳", "紐": "纽", "西": "西",
    "馬": "马", "來": "来", "泰": "泰", "越": "越", "尼": "尼",
    "亞": "亚", "印": "印", "度": "度", "巴": "巴", "西": "西",
    "墨": "墨", "阿": "阿", "根": "根", "廷": "廷", "愛": "爱",
    "爾": "尔", "葡": "葡", "萄": "萄", "牙": "牙", "希": "希",
    "臘": "腊", "土": "土", "耳": "耳", "其": "其", "以": "以",
    "色": "色", "列": "列", "埃": "埃", "及": "及", "南": "南",
    "非": "非", "智": "智", "利": "利", "捷": "捷", "克": "克",
    "奧": "奥", "地": "地", "比": "比", "時": "时", "冰": "冰",
    "島": "岛", "中": "中", "英": "英", "法": "法", "美": "美",
    "日": "日", "本": "本", "新": "新", "加": "加", "坡": "坡",
}


def _to_simplified(text: str) -> str:
    """把繁体国名字符逐个转简体。只处理国名里高频的字，不做全量转换。"""
    return "".join(_TRAD_CHARS.get(ch, ch) for ch in text)


def _norm_city(name: str) -> str:
    """城市名规范化：繁体转简体、英文转中文。"""
    if not name:
        return ""
    city = name.strip()
    if city in TRAD_TO_SIMP:
        return TRAD_TO_SIMP[city]
    # 繁体字转简体（紐約 → 纽约）
    city = _to_simplified(city)
    for trad, simp in (("臺", "台"), ("灣", "湾"), ("東", "东"),
                       ("紐", "纽"), ("約", "约"), ("爾", "尔"),
                       ("廣", "广"), ("蘇", "苏"), ("興", "兴")):
        city = city.replace(trad, simp)
    # 英文城市名转中文
    if not any("\u4e00" <= ch <= "\u9fff" for ch in city):
        low = city.lower()
        if low in PLACE_CN:
            return PLACE_CN[low]
    # 去掉「市」后缀，和省份的「省」后缀保持一致的紧凑风格
    if city.endswith("市") and len(city) > 1:
        city = city[:-1]
    return city


def _norm_province(value: str) -> str:
    """省/州名规范化：直辖市/自治区/特别行政区去后缀；英文州名转中文。"""
    if not value:
        return ""
    raw = value.strip()
    # 已经是中文：走全名→简称映射，拿不到就去掉常见后缀
    if any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        if raw in PROVINCE_CN_FULL:
            return PROVINCE_CN_FULL[raw]
        simp = _to_simplified(raw)
        if simp in PROVINCE_CN_FULL:
            return PROVINCE_CN_FULL[simp]
        for suf in ("特别行政区", "自治区", "壮族自治区", "回族自治区",
                   "维吾尔自治区", "维吾尔族自治区", "省", "市"):
            if simp.endswith(suf) and len(simp) > len(suf):
                return simp[:-len(suf)]
        return simp
    # 英文州/省名 → 中文
    low = raw.lower().strip()
    if low in PROVINCE_CN_EN:
        return PROVINCE_CN_EN[low]
    return raw


def _norm_district(value: str) -> str:
    """区/县名规范化：去掉「区/县/市辖区」后缀，方便紧凑显示。

    免费库普遍拿不到区/县，只有国内 IP 经 PConline 补齐后才有；拿不到就空着。
    """
    if not value:
        return ""
    d = value.strip()
    if d in ("市辖区", "县"):
        return ""
    for suf in ("市辖区", "新区", "区", "县", "市"):
        if d.endswith(suf) and len(d) > len(suf):
            return d[:-len(suf)]
    return d


def detect_datacenter(org: str, isp: str, as_field: str, hosting=None):
    """从运营商字段里识别机房。

    返回 (机房描述, 是否云机房, 是否CDN)。识别不出来就返回 org 原文。
    """
    blob = " ".join(x for x in (org, isp, as_field) if x).lower()
    if not blob.strip():
        return "", bool(hosting), False

    dc = ""
    for label, keys in DATACENTER_PATTERNS:
        if any(k in blob for k in keys):
            dc = label
            break

    is_cloud = bool(hosting) or any(k in blob for k in CLOUD_KEYWORDS)

    # CDN 判定要同时满足「命中 CDN 特征」且「不在排除名单里」，
    # 避免把云主机、骨干网、源站误标成 CDN。
    cdn = ""
    if not any(x in blob for x in CDN_EXCLUDE):
        for key, label in CDN_HINTS.items():
            if key in blob:
                cdn = label
                break

    # 描述优先用原 org（信息最全，常带地区后缀），没有再退回厂商名
    desc = (org or "").strip()
    if not desc:
        desc = (isp or "").strip()
    if not desc and dc:
        desc = dc
    return desc, is_cloud, bool(cdn)


# 行政区/城市名的英文 → 中文，用于「国家·城市」去重和显示统一
PLACE_CN = {
    "singapore": "新加坡", "hong kong": "香港", "macau": "澳门",
    "macao": "澳门", "taipei": "台北", "tokyo": "东京", "osaka": "大阪",
    "seoul": "首尔", "ashburn": "阿什本", "san francisco": "旧金山",
    "los angeles": "洛杉矶", "new york": "纽约", "seattle": "西雅图",
    "chicago": "芝加哥", "dallas": "达拉斯", "miami": "迈阿密",
    "london": "伦敦", "paris": "巴黎", "frankfurt": "法兰克福",
    "amsterdam": "阿姆斯特丹", "dublin": "都柏林", "sydney": "悉尼",
    "melbourne": "墨尔本", "mumbai": "孟买", "beijing": "北京",
    "shanghai": "上海", "guangzhou": "广州", "shenzhen": "深圳",
    "hangzhou": "杭州", "chengdu": "成都", "nanjing": "南京",
    "yangzhou": "扬州", "suzhou": "苏州", "wuxi": "无锡",
    "changzhou": "常州", "nantong": "南通", "xuzhou": "徐州",
    "hefei": "合肥", "jiaxing": "嘉兴", "ningbo": "宁波",
    "wenzhou": "温州", "qingdao": "青岛", "jinan": "济南",
    "zhengzhou": "郑州", "wuhan": "武汉", "changsha": "长沙",
    "xiamen": "厦门", "fuzhou": "福州", "tianjin": "天津",
    "chongqing": "重庆", "xian": "西安", "kunming": "昆明",
    "dalian": "大连", "shenyang": "沈阳", "harbin": "哈尔滨",
    "taiyuan": "太原", "shijiazhuang": "石家庄", "nanchang": "南昌",
    "guiyang": "贵阳", "lanzhou": "兰州", "urumqi": "乌鲁木齐",
    "haikou": "海口", "sanya": "三亚", "wuzhen": "乌镇",
    "zhangjiakou": "张家口", "langfang": "廊坊", "baoding": "保定",
    "toronto": "多伦多", "vancouver": "温哥华", "montreal": "蒙特利尔",
    "brisbane": "布里斯班", "south brisbane": "布里斯班",
    "virginia": "弗吉尼亚", "california": "加利福尼亚",
    "texas": "得克萨斯", "florida": "佛罗里达",
}

# 中文城市/地区名 → 去重用的规范键（英文、繁体、简体都归一到同一键）
_ALIAS_TO_KEY = {
    "singapore": "singapore", "新加坡": "singapore",
    "hong kong": "hongkong", "香港": "hongkong", "中国香港": "hongkong",
    "macau": "macau", "macao": "macau", "澳门": "macau", "中国澳门": "macau",
    "taiwan": "taiwan", "台湾": "taiwan", "中国台湾": "taiwan",
}


def _place_key(text: str) -> str:
    """把地名压成一个可比较的键，用于判断国家与城市是否其实同一地。"""
    if not text:
        return ""
    t = text.strip().lower()
    if t in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[t]
    # 去掉「中国」前缀再比，避免「中国香港」vs「香港」判不出相同
    stripped = t.replace("中国", "").strip()
    if stripped in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[stripped]
    return stripped


def _localize_place(name: str) -> str:
    """把英文城市名转中文；已经是中文的原样返回。"""
    if not name:
        return ""
    t = name.strip()
    if any("\u4e00" <= ch <= "\u9fff" for ch in t):
        if t in TRAD_TO_SIMP:
            return TRAD_TO_SIMP[t]
        return t
    key = t.lower()
    if key in PLACE_CN:
        return PLACE_CN[key]
    return t


def describe_region(geo: dict) -> str:
    """拼一句人话的地区描述：「日本 · 东京」。

    城邦国家（新加坡这类）城市名常与国家名相同（甚至一中一英），
    归一到同一个键后判重，只显示一次。
    """
    if not geo:
        return "未知位置"
    country = geo.get("country") or ""
    city = _localize_place(geo.get("city") or "")
    region = _localize_place(geo.get("region") or "")
    parts = []
    if country:
        parts.append(country)
    place = city or region
    if place:
        ck, pk = _place_key(country), _place_key(place)
        # 城市与国家其实是同一地（新加坡·Singapore / 中国香港·Hong Kong）就不重复
        if not (ck and pk and (ck == pk or ck.endswith(pk) or pk.endswith(ck))):
            # 「中国」+ 一省/一直辖市时，城市信息仍有价值，保留
            parts.append(place)
    return " · ".join(parts) if parts else "未知位置"


def describe_location(geo: dict) -> str:
    """拼「省 市 区」：缺哪一级就省略哪一级（区通常只有国内 IP 才有）。

    直辖市这类「省==市」的情况，城市一级会去重，只显示一个（如「北京 海淀」）。
    用于报告明细行与界面提示，比 describe_region 更贴近「地址精确到省市区」的需求。
    """
    if not geo:
        return "未知位置"
    prov = geo.get("province") or ""
    city = geo.get("city") or ""
    dist = geo.get("district") or ""
    parts = []
    if prov:
        parts.append(prov)
    if city and city != prov:
        parts.append(city)
    if dist:
        parts.append(dist)
    return " ".join(parts) if parts else "未知位置"


def describe_network(geo: dict) -> str:
    """拼一句人话的网络归属：「Microsoft Azure Cloud (southeastasia)」。"""
    if not geo:
        return ""
    dc = geo.get("datacenter") or ""
    isp = geo.get("isp") or ""
    if dc and isp and dc != isp:
        return f"{dc}（{isp}）"
    return dc or isp or ""


def short_network(geo: dict) -> str:
    if not geo:
        return ""
    return geo.get("datacenter") or geo.get("isp") or geo.get("org") or ""


# ---------------------------------------------------------------- HTTP 抓取


def _curl(url: str, timeout: int = HTTP_TIMEOUT, encoding: str = "utf-8"):
    """用系统 curl 取文本。找不到 curl 时用标准库降级。

    encoding 默认 utf-8；个别国内库（如 PConline）返回 GBK，需传 "gbk"。
    """
    exe = _find_curl()
    if exe:
        try:
            flags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                flags = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(
                [exe, "-s", "--max-time", str(timeout),
                 "-A", _UA, "--noproxy", "*", url],
                capture_output=True, timeout=timeout + 6,
                creationflags=flags,
            )
            body = (r.stdout or b"").decode(encoding, "replace").strip()
            if body:
                return body
        except (OSError, subprocess.SubprocessError):
            pass
    return _urllib_get(url, timeout)


def _find_curl():
    import shutil
    exe = shutil.which("curl")
    if exe:
        return exe
    for p in (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "curl.exe"),):
        if os.path.exists(p):
            return p
    return None


def _urllib_get(url: str, timeout: int = HTTP_TIMEOUT):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _json_of(text: str):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        # 有的库返回前后带杂质，尝试截取第一个 { 到最后一个 }
        i, j = text.find("{"), text.rfind("}")
        if 0 <= i < j:
            try:
                return json.loads(text[i:j + 1])
            except ValueError:
                return None
    return None


def _throttle():
    """全局限速，避免触发免费库的频率上限。"""
    with _rate_lock:
        now = time.time()
        wait = MIN_INTERVAL - (now - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()


# ---------------------------------------------------------------- 各库适配

def _try_ipapi(ip):
    """ip-api.com：字段最全，含中文、isp、org、as、hosting。免费档限 45/分钟。"""
    body = _curl(
        f"http://ip-api.com/json/{ip}"
        f"?lang=zh-CN&fields=status,message,country,countryCode,regionName,"
        f"city,isp,org,as,hosting,lat,lon,timezone,query"
    )
    d = _json_of(body)
    if not d or d.get("status") != "success":
        return None
    return {
        "country": d.get("country") or "",
        "country_code": (d.get("countryCode") or "").upper(),
        "region": d.get("regionName") or "",
        "city": d.get("city") or "",
        "isp": d.get("isp") or "",
        "org": d.get("org") or "",
        "as": d.get("as") or "",
        "hosting": d.get("hosting"),
        "lat": d.get("lat"), "lon": d.get("lon"),
        "timezone": d.get("timezone") or "",
        "source": "ip-api.com",
    }


def _try_ipwho(ip):
    """ipwho.is：无需 key，字段较全，且不返回中文。"""
    body = _curl(f"https://ipwho.is/{ip}")
    d = _json_of(body)
    if not d or not d.get("success", True):
        return None
    conn = d.get("connection") or {}
    return {
        "country": d.get("country") or "",
        "country_code": (d.get("country_code") or "").upper(),
        "region": d.get("region") or "",
        "city": d.get("city") or "",
        "isp": conn.get("isp") or "",
        "org": conn.get("org") or "",
        "as": f"AS{conn.get('asn')} {conn.get('org') or ''}".strip(),
        "hosting": None,
        "lat": d.get("latitude"), "lon": d.get("longitude"),
        "timezone": (d.get("timezone") or {}).get("id", ""),
        "source": "ipwho.is",
    }


def _try_ipsb(ip):
    """api.ip.sb：字段干净，org/asn 分列，机房信息常带厂商全名。"""
    body = _curl(f"https://api.ip.sb/geoip/{ip}")
    d = _json_of(body)
    if not d or d.get("ip") is None and not d.get("country"):
        return None
    return {
        "country": d.get("country") or "",
        "country_code": (d.get("country_code") or "").upper(),
        "region": d.get("region") or "",
        "city": d.get("city") or "",
        "isp": d.get("isp") or "",
        "org": d.get("organization") or d.get("asn_organization") or "",
        "as": f"AS{d.get('asn')} {d.get('asn_organization') or ''}".strip(),
        "hosting": None,
        "lat": d.get("latitude"), "lon": d.get("longitude"),
        "timezone": d.get("timezone") or "",
        "source": "api.ip.sb",
    }


def _try_ipapico(ip):
    """ipapi.co：无需 key，附带所属网段，对判定云机房有帮助。"""
    body = _curl(f"https://ipapi.co/{ip}/json/")
    d = _json_of(body)
    if not d or d.get("error"):
        return None
    return {
        "country": d.get("country_name") or "",
        "country_code": (d.get("country_code") or "").upper(),
        "region": d.get("region") or "",
        "city": d.get("city") or "",
        "isp": d.get("org") or "",
        "org": d.get("org") or "",
        "as": d.get("asn") or "",
        "network": d.get("network") or "",
        "hosting": None,
        "lat": d.get("latitude"), "lon": d.get("longitude"),
        "timezone": d.get("timezone") or "",
        "source": "ipapi.co",
    }


def _try_ipinfo(ip):
    """ipinfo.io：无需 key 时字段较少，但常带 hostname，能反推服务身份。"""
    body = _curl(f"https://ipinfo.io/{ip}/json")
    d = _json_of(body)
    if not d or d.get("bogon"):
        return None
    loc = d.get("loc") or ""
    lat = lon = None
    if "," in loc:
        try:
            lat, lon = float(loc.split(",")[0]), float(loc.split(",")[1])
        except (ValueError, IndexError):
            lat = lon = None
    return {
        "country": d.get("country") or "",
        "country_code": (d.get("country") or "").upper(),
        "region": d.get("region") or "",
        "city": d.get("city") or "",
        "isp": d.get("org") or "",
        "org": d.get("org") or "",
        "as": d.get("org") or "",
        "hostname": d.get("hostname") or "",
        "hosting": None,
        "lat": lat, "lon": lon,
        "timezone": d.get("timezone") or "",
        "source": "ipinfo.io",
    }


def _try_pconline(ip):
    """PConline ipJson：无需 key，对国内 IP 返回 省(pro)/市(city)/区(region) 中文。

    免费库普遍拿不到「区」，这一家正好补齐；且对国内 IP 的省市区最准。
    注意返回体是 **GBK 编码**，_curl 要传 encoding="gbk" 才能正确解中文。
    """
    # json=true 让接口直接返回纯 JSON（否则是 `var ipJson = {...};` 包裹）
    body = _curl(
        f"https://whois.pconline.com.cn/ipJson.jsp?ip={ip}&json=true",
        encoding="gbk")
    d = _json_of(body)
    if not d:
        return None
    if d.get("err") not in ("", None) and not (d.get("pro") or d.get("city")):
        return None
    pro = (d.get("pro") or "").strip()
    city = (d.get("city") or "").strip()
    region = (d.get("region") or "").strip()  # 区/县
    if not pro and not city and not region:
        return None
    return {
        "region": pro,        # 省/直辖市
        "city": city,        # 市
        "district": region,  # 区/县
        "source_extra": "pconline",
    }


# 回退顺序：先中文优先的，再备用的。
# ip-api 放第一个（有中文 + hosting 标记），ipinfo 放最后（字段最少但最稳）。
PROVIDERS = [_try_ipapi, _try_ipwho, _try_ipsb, _try_ipapico, _try_ipinfo]


# ---------------------------------------------------------------- 对外接口


def _enrich_cn(geo: dict) -> dict:
    """用 PConline 给国内 IP 补全/校正 省/市/区。

    免费库普遍拿不到「区」，PConline 正好补齐；且它对国内 IP 的省市区最准。
    只在 country=="中国" 时调用，避免用 PConline 的海外数据覆盖更准的其它库结果。
    失败则原样返回（district 留空，界面显示「—」）。
    """
    if geo.get("country") != "中国":
        return geo
    try:
        _throttle()
        pc = _try_pconline(geo.get("ip", ""))
    except Exception:
        return geo
    if not pc:
        return geo
    if pc.get("region"):
        geo["province"] = _norm_province(pc["region"])
        geo["region"] = geo["province"]
    if pc.get("city"):
        geo["city"] = _norm_city(pc["city"])
    if pc.get("district"):
        geo["district"] = _norm_district(pc["district"])
    return geo


def lookup(ip: str, use_cache: bool = True, verbose: bool = False) -> dict:
    """查一个 IP 的地理位置与机房。

    返回 dict；查不到时返回 {"ok": False, "error": ...}。
    永不抛异常——网络查询失败是常态，调用方不该被异常打断。
    """
    ip = (ip or "").strip()
    if not ip:
        return {"ok": False, "ip": "", "error": "空 IP"}

    # 本机 / 私网地址不做地理查询，没有意义
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback:
            return {"ok": True, "ip": ip, "country": "本机", "city": "",
                    "region": "", "datacenter": "本机环回地址",
                    "is_cloud": False, "is_cdn": False, "source": "local"}
        if addr.is_private:
            return {"ok": True, "ip": ip, "country": "局域网", "city": "",
                    "region": "", "datacenter": "私有网段",
                    "is_cloud": False, "is_cdn": False, "source": "local"}
    except ValueError:
        return {"ok": False, "ip": ip, "error": "IP 格式非法"}

    if use_cache:
        hit = _cache_get(ip)
        if hit is not None:
            out = dict(hit)
            out.pop("_ts", None)
            out["cached"] = True
            return out

    last_err = "所有查询库均未返回结果"
    for fn in PROVIDERS:
        try:
            _throttle()
            raw = fn(ip)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{fn.__name__}: {exc}"
            continue
        if not raw:
            continue

        geo = _finalize(ip, raw)
        # 国内 IP 再让 PConline 补一层 省/市/区（尤其区，其它库普遍没有）
        geo = _enrich_cn(geo)
        if verbose:
            print(f"  {ip} <- {geo.get('source')}")
        if use_cache:
            _cache_put(ip, geo)
        return geo

    return {"ok": False, "ip": ip, "error": last_err}


def _finalize(ip: str, raw: dict) -> dict:
    """把各库的原始返回统一成我们的字段，并做合规化处理。"""
    country = normalize_country(raw.get("country", ""))
    province = _norm_province(raw.get("region", ""))
    city = _norm_city(raw.get("city", ""))
    district = _norm_district(raw.get("district", ""))
    isp = (raw.get("isp") or "").strip()
    org = (raw.get("org") or "").strip()
    as_field = (raw.get("as") or "").strip()
    hosting = raw.get("hosting")

    dc, is_cloud, is_cdn = detect_datacenter(org, isp, as_field, hosting)

    return {
        "ok": True,
        "ip": ip,
        "country": country,
        "country_code": raw.get("country_code", ""),
        "region": province,        # 省（与 province 同源，供 describe_region 兼容）
        "province": province,
        "city": city,
        "district": district,
        "isp": isp,
        "org": org,
        "as": as_field,
        "datacenter": dc,
        "is_cloud": is_cloud,
        "is_cdn": is_cdn,
        "hostname": raw.get("hostname", ""),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "timezone": raw.get("timezone", ""),
        "network": raw.get("network", ""),
        "source": raw.get("source", ""),
    }


def lookup_many(ips, use_cache: bool = True, workers: int = 4,
                progress=None, should_stop=None, verbose: bool = False) -> dict:
    """并发查一批 IP。返回 {ip: geo}。

    workers 不宜太大：免费库按分钟限流，并发过高反而全部失败。
    """
    ips = [ip for ip in dict.fromkeys(ips) if ip]
    result = {}
    if not ips:
        return result

    total = len(ips)
    done = [0]
    lock = threading.Lock()

    def work(ip):
        if should_stop is not None and should_stop():
            return
        geo = lookup(ip, use_cache=use_cache, verbose=verbose)
        with lock:
            result[ip] = geo
            done[0] += 1
            if progress is not None:
                try:
                    progress(done[0], total, ip, geo)
                except Exception:  # noqa: BLE001
                    pass

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, ips))
    return result


def lookup_stream(ips, use_cache: bool = True, workers: int = 4,
                  should_stop=None, verbose: bool = False):
    """并发查一批 IP，**每查完一个就 yield 一个**。

    产出 `(done, total, ip, geo)`，done 从 1 开始计数。

    与 lookup_many 的区别只在于交付方式：调用方可以在收到第一个结果时
    就把它显示出来，而不是等整批查完。界面上的「查到什么就显示什么」
    就是靠这个实现——慢的库会拖住整批，先到的结果没理由扣着不显示。

    生成器里不能用 ThreadPoolExecutor 的 as_completed（它是阻塞迭代），
    这里用 queue + 后台提交线程，保证 yield 是即时的。
    """
    ips = [ip for ip in dict.fromkeys(ips) if ip]
    if not ips:
        return
    total = len(ips)
    out = queue.Queue()

    def work(ip):
        if should_stop is not None and should_stop():
            out.put((ip, None))
            return
        try:
            geo = lookup(ip, use_cache=use_cache, verbose=verbose)
        except Exception as exc:  # noqa: BLE001
            geo = {"ok": False, "ip": ip, "error": f"{type(exc).__name__}: {exc}"}
        out.put((ip, geo))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, ip) for ip in ips]
        done = 0
        for _ in range(total):
            ip, geo = out.get()
            done += 1
            if geo is not None:
                yield done, total, ip, geo
        # 等线程收干净，避免残留线程在解释器退出时刷警告
        for f in futures:
            f.cancel()


# ---------------------------------------------------------------- 命令行


def _main(argv):
    args = [a for a in argv if not a.startswith("--")]
    verbose = "--verbose" in argv or "-v" in argv

    if not args:
        print(__doc__)
        print("用法：python geoloc.py <IP> [IP ...]")
        print("      python geoloc.py --cache-info | --clear-cache")
        print("      python geoloc.py --batch <文件，每行一个IP>")
        return 0

    if "--cache-info" in argv:
        s = cache_stats()
        print(f"缓存：共 {s['total']} 条，有效 {s['fresh']} 条，"
              f"过期/旧版 {s['stale']} 条（结构版本 v{s['version']}）")
        print(f"位置：{CACHE_FILE}")
        return 0

    if "--clear-cache" in argv:
        n = clear_cache()
        print(f"已清空 {n} 条缓存")
        return 0

    if "--batch" in argv:
        idx = argv.index("--batch")
        if idx + 1 >= len(argv):
            print("--batch 后面要跟文件名")
            return 1
        with open(argv[idx + 1], encoding="utf-8") as f:
            ips = [l.strip() for l in f if l.strip()]
    else:
        ips = args

    print(f"查询 {len(ips)} 个 IP…\n")
    print(f"{'IP':<18} {'地区':<20} {'运营商 / 机房':<44} {'来源'}")
    print("-" * 100)
    geos = lookup_many(ips, verbose=verbose)
    for ip in ips:
        g = geos.get(ip) or {}
        if g.get("ok"):
            print(f"{ip:<18} {describe_region(g):<20} "
                  f"{short_network(g)[:44]:<44} {g.get('source','')}")
        else:
            print(f"{ip:<18} {'查询失败':<20} {g.get('error','')[:44]}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
