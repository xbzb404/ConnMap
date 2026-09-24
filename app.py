# -*- coding: utf-8 -*-
"""本机连接地图（ConnMap）——界面层。

列出本机所有应用（含系统组件）当前连接的服务器，反查每个服务器 IP
的地理位置与所属机房，回答「我这个软件连的服务器到底在哪个国家/机房」。

界面为浅色卡片式设计，与 netdiagnose 保持同一套视觉语言。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import connscan
import geoloc
import winproc
from connscan import (
    Connection, NET_LABEL, NET_LAN, NET_LOCAL, NET_PUBLIC,
    human_proc_name, proc_category,
)

APP_NAME = "本机连接地图"
APP_SUBTITLE = "看每个应用连到了哪个国家 / 机房"
APP_VERSION = "1.0"
AUTHOR = "by zhb"

# ---------------------------------------------------------------- 设计 token

C = {
    "bg":           "#F7F8FA",
    "surface":      "#FFFFFF",
    "surface_alt":  "#FAFBFC",
    "border":       "#E5E7EB",
    "border_soft":  "#F0F1F3",
    "text":         "#1F2329",
    "text_sub":     "#646A73",
    "text_muted":   "#8F959E",
    "accent":       "#2B7FFF",
    "accent_hover": "#1F6FEB",
    "accent_soft":  "#EAF2FF",
    "ok":           "#00B42A",
    "ok_soft":      "#E8FFEA",
    "warn":         "#FF7D00",
    "warn_soft":    "#FFF7E8",
    "fail":         "#F53F3F",
    "fail_soft":    "#FFECE8",
    "info":         "#2B7FFF",
    "info_soft":    "#EAF2FF",
    "skip":         "#8F959E",
    "skip_soft":    "#F2F3F5",
    "track":        "#EEF0F3",
    "row_hover":    "#F5F8FF",
}

# 地区 → 强调色。用于地图/汇总上色，让「连到哪」一眼可辨。
REGION_COLORS = [
    "#2B7FFF", "#00B42A", "#FF7D00", "#F53F3F", "#722ED1",
    "#13C2C2", "#EB2F96", "#FAAD14", "#52C41A", "#2F54EB",
]

# 常见的地区配色偏好（让中国/美国/日本这类高频结果颜色稳定）
REGION_COLOR_FIXED = {
    "中国": "#2B7FFF",
    "中国香港": "#13C2C2",
    "中国台湾": "#13C2C2",
    "中国澳门": "#13C2C2",
    "美国": "#722ED1",
    "日本": "#F53F3F",
    "新加坡": "#00B42A",
    "韩国": "#EB2F96",
    "德国": "#FAAD14",
    "英国": "#2F54EB",
    "俄罗斯": "#FF7D00",
    "加拿大": "#EB2F96",
    "澳大利亚": "#FAAD14",
    "法国": "#2F54EB",
    "荷兰": "#FF7D00",
}

FONT_FAMILY = "Microsoft YaHei UI"
MONO_FAMILY = "Consolas"


def pick_font(root: tk.Tk, candidates, fallback: str) -> str:
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return fallback


def region_color(country: str, index: int = 0) -> str:
    if country in REGION_COLOR_FIXED:
        return REGION_COLOR_FIXED[country]
    return REGION_COLORS[index % len(REGION_COLORS)]


# 按「像素宽」截断文字，而不是按字符数。
# 按字符数截（`s[:34]`）在中英混排下会截在半个词上，看着像乱码
# （「PPLive(tianjin」这种）；而且中文 34 字和英文 34 字宽度差一倍，
# 用同一个字符数上限必然一边浪费一边溢出。改成量到装不下为止，
# 并且补省略号 —— 让「被截断」看起来是有意为之，不是渲染坏了。
_FIT_CACHE = {}


def fit_text(text: str, font, max_px: int, ellipsis: str = "…") -> str:
    """把 text 压到 max_px 像素内，超长时末尾换省略号。

    font 传 tkfont.Font（调用方自己缓存实例，别在这里新建）。
    """
    if not text:
        return text
    if max_px <= 0:
        return ""
    key = (font.actual("family"), font.actual("size"),
           font.actual("weight"), text, max_px)
    hit = _FIT_CACHE.get(key)
    if hit is not None:
        return hit
    if font.measure(text) <= max_px:
        _FIT_CACHE[key] = text
        return text
    ell_w = font.measure(ellipsis)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid]) + ell_w <= max_px:
            lo = mid
        else:
            hi = mid - 1
    out = text[:lo] + ellipsis if lo else ellipsis
    _FIT_CACHE[key] = out
    return out


# tkfont.Font 实例要复用：每行每列都新建一个，几百行下来会明显拖慢铺表。
# 按 (master, 字体描述) 缓存，master 是控件（挂在它身上随控件一起回收）。
def _font_of(master, spec):
    """把 ("Microsoft YaHei UI", 9) 这类字体描述换成可 measure 的 Font。"""
    if isinstance(spec, tkfont.Font):
        return spec
    cache = getattr(master, "_font_cache", None)
    if cache is None:
        cache = {}
        try:
            master._font_cache = cache
        except Exception:  # noqa: BLE001
            pass
    key = tuple(spec) if isinstance(spec, (tuple, list)) else (spec,)
    f = cache.get(key)
    if f is None:
        f = tkfont.Font(root=master, font=spec)
        cache[key] = f
    return f


def fit_net_text(text: str, font, max_px: int, ellipsis: str = "…") -> str:
    """网络列的截断：**优先保住末尾的 [CDN] / [云机房] 标签**。

    这列的值形如「Huawei Cloud Service　[云机房]」—— 运营商全名可以很长，
    但真正有信息量的是末尾那个类型标签。若按普通方式从头截，
    标签正好是被砍掉的那一截（最没用的名字留着、最有用的标签丢了）。

    策略：先保证「标签 + 至少几名名字」都能放下；名字不够位就在标签前
    保留一个尽量长的前缀并补省略号。名字若连一个字都放不下，
    再退成只显示标签（这时标签才是唯一有效信息）。
    """
    if not text:
        return text
    # 标签分隔可能是全角空格（_network_display 拼的）也可能是半角空格
    # （网速库原始字段里就带），两种都要认；否则标签识别不出来，
    # 截断时会把最有信息量的 [CDN]/[云机房] 一起砍掉。
    tag = ""
    name = text
    for sep in ("　[", " ["):
        if text.endswith("]") and sep in text:
            head, _, tail = text.rpartition(sep)
            # 分隔符前面可能还有多余空格，去掉免得占位
            name, tag = head.rstrip(), "[" + tail
            break
    if not tag:
        return fit_text(text, font, max_px, ellipsis)

    # 统一用全角空格拼接（半角那种来源不齐，混着用对齐会跳）
    joined = name + "　" + tag
    if font.measure(joined) <= max_px:
        return joined

    tag_w = font.measure(tag)
    # 标签自己都塞不下：连标签一起按普通规则截（极窄列的兜底）
    if tag_w > max_px:
        return fit_text(text, font, max_px, ellipsis)

    # 名字可用宽 = 总宽 - 全角空格 - 标签宽
    room = max_px - font.measure("　") - tag_w
    if room <= 0:
        return tag
    short = fit_text(name, font, room, ellipsis)
    # 名字只剩省略号（一个字都没放下）时，别在标签前留个孤零零的「…」，
    # 直接给标签更干净，也避免「…　[云机房]」这种看着像出错的显示。
    if short == ellipsis or not short:
        return tag
    out = short + "　" + tag
    if font.measure(out) > max_px:      # 浮点/取整误差兜底
        return tag if font.measure(tag) <= max_px else \
            fit_text(out, font, max_px, ellipsis)
    return out


# ---------------------------------------------------------------- 基础控件

# 抗锯齿倍率。Tk 的 create_oval / create_arc / y 方向圆角是没有抗锯齿的，
# 在浅色背景上会看到明显的像素毛边（进度环、占比条、胶囊标签最明显）。
# 做法：在 canvas 内部按 SS 倍尺寸绘制，再用 ImageTk 缩放回目标尺寸贴图。
SS = 4


def _tk_ok() -> bool:
    """Tk 的 PhotoImage 支持 zoom/subsample，但 subsample 只能是整数。

    为了让 SS 倍缩小得到匀速采样，先把图 Zoom 到 SS 的整数倍。
    """
    return True


def _has_imagetk():
    try:
        from PIL import ImageTk  # noqa: F401
    except Exception:  # noqa: BLE001
        return None
    return ImageTk


def _get_imagetk():
    """把 PIL.ImageTk 暴露给 app 模块。

    本环境的删除钩子会让 ImageTk 的实例缓存机制在某些时机报错，
    因此这里只做一次导入并缓存；失败则退回纯 Tk 绘制（有毛边但不崩）。
    """
    global _IMAGETK
    if _IMAGETK is _UNSET:
        _IMAGETK = _has_imagetk()
    return _IMAGETK


_UNSET = object()
_IMAGETK = _UNSET


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ---- 抗锯齿绘制 ----------------------------------------------------
#
# Tk 的 create_oval / create_arc 完全没有抗锯齿，`create_polygon(smooth=True)`
# 只在 x 方向平滑、y 方向仍有台阶。浅色背景上这些毛边非常显眼（进度环最明显），
# 整块界面会显得廉价。
#
# 方案：用 Pillow 在 SS 倍尺寸上画（Pillow 自带抗锯齿），再 LANCZOS 缩回目标尺寸，
# 转成 PhotoImage 贴在 canvas 上。没有 Pillow 时退回 Tk 分块梯形近似（也无毛边）。

SS = 4          # 超采样倍率
_SS_STEPS = 72  # 环形每圈的分段数

try:
    from PIL import Image, ImageDraw, ImageTk
    _HAS_PIL = True
except Exception:  # noqa: BLE001
    Image = ImageDraw = ImageTk = None
    _HAS_PIL = False


def _rgb(hexcolor: str):
    h = (hexcolor or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class SsPainter:
    """离屏抗锯齿绘制器。

    在 k 倍尺寸上作画，落盘前 LANCZOS 缩小，因此弧线、圆角、斜线都没有台阶。
    调用方一律用**逻辑坐标**，本类负责乘 k。
    """

    def __init__(self, w: int, h: int, bg: str, k: int = SS):
        self.w, self.h = int(w), int(h)
        self.k = max(1, int(k))
        self.ow, self.oh = self.w * self.k, self.h * self.k
        self.bg = bg
        if _HAS_PIL:
            self.img = Image.new("RGB", (self.ow, self.oh), _rgb(bg))
            self.d = ImageDraw.Draw(self.img)
            self.tkoff = None
        else:
            # 无 Pillow：退回离屏 Tk Canvas，用分块梯形近似圆角/圆弧
            self.tkoff = tk.Canvas(None, width=self.ow, height=self.oh,
                                   highlightthickness=0, bd=0, bg=bg)
            self.d = None

    # -- 圆角矩形（用 Pillow 就是真圆角，退回 Tk 就画梯形多边形）--

    def rrect(self, x1, y1, x2, y2, r, fill, outline=None, width=1):
        k = self.k
        box = [x1 * k, y1 * k, x2 * k, y2 * k]
        if self.d is not None:
            self.d.rounded_rectangle(
                box, radius=max(0, r * k), fill=_rgb(fill) if fill else None,
                outline=_rgb(outline) if outline else None,
                width=max(1, int(width * k)))
        else:
            _round_rect(self.tkoff, *box, max(0.5, r * k),
                        fill=fill or "", outline=outline or "", width=width * k)

    # -- 圆环弧（进度环）------------------------------------------

    def ring(self, cx, cy, radius, width, start_deg, extent_deg, color):
        """画一段圆环。extent 为负表示顺时针（与 Tk create_arc 语义一致）。"""
        if abs(extent_deg) < 0.05:
            return
        k = self.k
        R, w = radius * k, width * k
        steps = max(2, int(_SS_STEPS * min(1.0, abs(extent_deg) / 360.0)))
        if self.d is not None:
            import math
            a0 = math.radians(start_deg)
            # Pillow 角度顺时针为正、0° 指向 3 点方向；Tk 逆时针为正、0° 指 3 点。
            # 用「外圈点 + 内圈点」构成扇形环，逐段画线保证端点圆润。
            def pt(a, rr):
                return (cx * k + rr * math.cos(a), cy * k - rr * math.sin(a))
            outer, inner = R + w / 2, R - w / 2
            prev = None
            for i in range(steps + 1):
                a = a0 + math.radians(extent_deg) * (i / steps)
                o, n = pt(a, outer), pt(a, inner)
                if prev is not None:
                    self.d.polygon([prev[0], prev[1], o[0], o[1],
                                    n[0], n[1], prev[2], prev[3]],
                                   fill=_rgb(color))
                prev = (o[0], o[1], n[0], n[1])
        else:
            self.tkoff.create_arc(cx * k - R, cy * k - R, cx * k + R, cy * k + R,
                                  start=start_deg, extent=extent_deg,
                                  outline=color, width=max(1, int(w)),
                                  style="arc")

    # -- 出图 -----------------------------------------------------

    def to_photo(self, master):
        if not _HAS_PIL:
            # 无 Pillow 的退路：拷离屏内容后整数倍 subsample
            try:
                pic = tk.PhotoImage(width=self.ow, height=self.oh,
                                    master=master)
                pic.tk.call(pic, "copy", str(self.tkoff),
                            "-compositingrule", "set")
                if self.k > 1:
                    pic = pic.subsample(self.k, self.k)
                return pic
            except tk.TclError:
                return None

        small = self.img.resize((self.w, self.h), Image.LANCZOS)
        try:
            return ImageTk.PhotoImage(small, master=master)
        except Exception:  # noqa: BLE001
            return None

    def release(self):
        try:
            if not _HAS_PIL and self.tkoff is not None:
                self.tkoff.destroy()
        except tk.TclError:
            pass


class AaCanvas(tk.Canvas):
    """抗锯齿画布基类。

    子类只实现 `_paint(p, w, h)`，用逻辑坐标在 SsPainter 上作画；
    基类负责尺寸变化时重建并贴图。文字用 Tk 原生 create_text 叠在上面
    （文字本来就有抗锯齿，不必走离屏）。
    """

    def __init__(self, master, width, height, bg, **kw):
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bd=0, bg=bg, **kw)
        self._bg = bg
        self._photo = None
        self.bind("<Configure>", lambda e: self._redraw())
        self.after(1, self._redraw)

    def _redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 3 or h < 3:
            w, h = int(self["width"]), int(self["height"])
        if w < 3 or h < 3:
            return
        p = SsPainter(w, h, self._bg)
        try:
            self._paint(p, w, h)
        finally:
            p.release()
        img = p.to_photo(self)
        if img is not None:
            self._photo = img
            self.create_image(0, 0, image=img, anchor="nw")
        self._paint_text(w, h)

    def _paint(self, p, w, h):
        """子类覆写：在 p 上作画（逻辑坐标）。"""

    def _paint_text(self, w, h):
        """子类覆写：用 Tk 文字叠加（可选）。"""


class RoundedFrame(tk.Canvas):
    """带圆角与可选描边的容器。"""

    def __init__(self, master, radius=10, fill=C["surface"], outline=C["border"],
                 stroke=1, padding=0, **kw):
        super().__init__(master, highlightthickness=0, bd=0,
                         bg=kw.pop("bg", C["bg"]), **kw)
        self.radius = radius
        self._fill = fill
        self._outline = outline
        self._stroke = stroke
        self.padding = padding
        self.body = tk.Frame(self, bg=fill)
        self._photo = None
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _evt=None):
        self.delete("shape")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        r = min(self.radius, w // 2, h // 2)
        pad = self._stroke
        p = SsPainter(w, h, self.cget("bg"))
        try:
            if self._stroke:
                # 先铺描边色整块，再叠内缩的填充色，得到 1px 平滑描边
                p.rrect(0, 0, w - 1, h - 1, r, self._outline)
                p.rrect(pad, pad, w - 1 - pad, h - 1 - pad,
                        max(1, r - pad), self._fill)
            else:
                p.rrect(0, 0, w - 1, h - 1, r, self._fill)
        finally:
            p.release()
        img = p.to_photo(self)
        if img is not None:
            self._photo = img
            self.create_image(0, 0, image=img, anchor="nw", tags="shape")
        self.tag_lower("shape")
        self.body.place(x=self.padding + pad, y=self.padding + pad,
                        width=max(1, w - 2 * (self.padding + pad)),
                        height=max(1, h - 2 * (self.padding + pad)))


class FlatButton(AaCanvas):
    """扁平按钮：主按钮填充强调色，次按钮描边。"""

    def __init__(self, master, text, command, *, primary=False,
                 btn_width=112, btn_height=36, font=None, bg=C["bg"]):
        super().__init__(master, btn_width, btn_height, bg, cursor="hand2")
        self.text = text
        self.command = command
        self.primary = primary
        self._font = font
        self._bw, self._bh = btn_width, btn_height
        self._enabled = True
        self._hover = False
        self._radius = 8
        self._fg = C["text"]
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _paint(self, p, w, h):
        if not self._enabled:
            fill, line, self._fg = C["skip_soft"], C["border"], C["text_muted"]
        elif self.primary:
            fill = C["accent_hover"] if self._hover else C["accent"]
            line, self._fg = fill, "#FFFFFF"
        else:
            fill = C["border_soft"] if self._hover else C["surface"]
            line = C["border"] if not self._hover else C["text_muted"]
            self._fg = C["text"]
        p.rrect(0.5, 0.5, w - 0.5, h - 0.5, self._radius,
                fill, outline=line, width=1)

    def _paint_text(self, w, h):
        self.create_text(w / 2, h / 2, text=self.text, fill=self._fg,
                         font=self._font)

    def _on_enter(self, _e):
        self._hover = True
        if self._enabled:
            self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        if self._enabled:
            self._redraw()

    def _on_click(self, _e):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, flag: bool):
        self._enabled = flag
        self.configure(cursor="hand2" if flag else "arrow")
        self._redraw()

    def set_text(self, text: str):
        self.text = text
        self._redraw()


class Chip(AaCanvas):
    """可点击的胶囊标签，用于筛选。"""

    def __init__(self, master, text, command, fonts, *, active=False,
                 color=None, bg=C["bg"]):
        self._font = fonts["small"]
        w = tkfont.Font(font=self._font).measure(text) + 26
        super().__init__(master, w, 28, bg, cursor="hand2")
        self.text = text
        self.command = command
        self.active = active
        self.color = color or C["accent"]
        self._bw, self._bh = w, 28
        self._hover = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def _paint(self, p, w, h):
        if self.active:
            fill, line, self._fg = self.color, self.color, "#FFFFFF"
        elif self._hover:
            fill, line, self._fg = C["accent_soft"], self.color, self.color
        else:
            fill, line, self._fg = C["surface"], C["border"], C["text_sub"]
        p.rrect(0.5, 0.5, w - 0.5, h - 0.5, (h - 1) / 2,
                fill, outline=line, width=1)

    def _paint_text(self, w, h):
        self.create_text(w / 2, h / 2, text=self.text,
                         fill=getattr(self, "_fg", C["text_sub"]),
                         font=self._font)

    def _enter(self, _e):
        self._hover = True
        self._redraw()

    def _leave(self, _e):
        self._hover = False
        self._redraw()

    def _click(self, _e):
        if self.command:
            self.command()


class CheckBox(AaCanvas):
    """自绘复选框。"""

    def __init__(self, master, text, var, fonts, *, bg=C["bg"]):
        self._font = fonts["small"]
        tw = tkfont.Font(font=self._font).measure(text)
        super().__init__(master, tw + 30, 26, bg, cursor="hand2")
        self.text = text
        self.var = var
        self._bw, self._bh = tw + 30, 26
        self.bind("<Button-1>", self._toggle)

    def _toggle(self, _e=None):
        self.var.set(not self.var.get())
        self._redraw()

    def _paint(self, p, w, h):
        box = 16
        y = (h - box) / 2
        if self.var.get():
            p.rrect(2, y, 2 + box, y + box, 5, C["accent"])
            if p.d is not None:
                k = p.k
                p.d.line([(6.8 * k, (y + 8.2) * k),
                          (9.5 * k, (y + 11.0) * k),
                          (13.3 * k, (y + 5.4) * k)],
                         fill=_rgb("#FFFFFF"), width=max(2, int(1.8 * k)),
                         joint="curve")
            else:
                p.tkoff.create_line(6.8 * p.k, (y + 8.2) * p.k,
                                    9.5 * p.k, (y + 11.0) * p.k,
                                    13.3 * p.k, (y + 5.4) * p.k,
                                    fill="#FFFFFF", width=2,
                                    capstyle="round", joinstyle="round")
        else:
            p.rrect(2, y, 2 + box, y + box, 5, C["surface"],
                    outline=C["border"], width=1)

    def _paint_text(self, w, h):
        self.create_text(28, h / 2, text=self.text,
                         fill=C["text_sub"], font=self._font, anchor="w")


class Ring(AaCanvas):
    """进度环。抗锯齿出图，圆弧无台阶。

    环内文字不居中堆叠 —— 「100%」+「已定位」两行塞进内圆会互相压。
    改成：环外右侧那一列本来就有统计数字，环内只放百分比，副标题放在
    环下沿外侧（sub 非空时控件自动加高，给副标题留出空间）。
    """

    def __init__(self, master, size=54, bg=C["surface"], sub_h=0):
        super().__init__(master, size, size + sub_h, bg)
        self._size = size
        self._sub_h = sub_h
        self._value = 0.0
        self._label = "0%"
        self._sub = ""

    def set_value(self, v: float, label=None, sub=None):
        self._value = max(0.0, min(1.0, v))
        self._label = label if label is not None else f"{int(round(v * 100))}%"
        if sub is not None:
            self._sub = sub
        self._redraw()

    def _paint(self, p, w, h):
        # 只在「环」这块方形区域内画，下方留给副标题
        s = min(w, self._size)
        pad = 5
        ring_w = 6
        cx = cy = s / 2
        radius = (s - 2 * pad - ring_w) / 2
        col = C["accent"] if self._value < 1 else C["ok"]
        p.ring(cx, cy, radius, ring_w, 0, 360, C["track"])
        if self._value > 0:
            p.ring(cx, cy, radius, ring_w, 90, -359.99 * self._value, col)

    def _paint_text(self, w, h):
        s = min(w, self._size)
        # 百分比居中在环内。字号用「实测文字宽」反推，不用比例估算 ——
        # 按比例算在中文字体下必然贴壁（字宽随字号非线性变化）。
        pad, ring_w = 5, 6
        # 环内可用弦宽：内圆直径再留一点余量，避免视觉上贴到环壁
        avail = (s - 2 * (pad + ring_w)) * 0.92
        fs = 11
        for cand in (14, 13, 12, 11, 10, 9, 8):
            if self._measure(self._label, cand) <= avail:
                fs = cand
                break
        self.create_text(s / 2, s / 2, text=self._label,
                         fill=C["text"], font=(FONT_FAMILY, fs, "bold"))
        if self._sub:
            self.create_text(w / 2, s + 2, text=self._sub, anchor="n",
                             fill=C["text_muted"], font=(FONT_FAMILY, 8))

    def _measure(self, text, size):
        """量文字实际像素宽（带缓存，避免每帧新建 Font 对象）。"""
        key = (size, text)
        cache = getattr(self, "_fmttmp", None)
        if cache is None:
            self._fmttmp = {}
            cache = self._fmttmp
        f = cache.get(size)
        if f is None:
            f = tkfont.Font(family=FONT_FAMILY, size=size, weight="bold")
            cache[size] = f
        return f.measure(text)


class Bar(AaCanvas):
    """横向占比条，用于地区分布。"""

    def __init__(self, master, height=8, bg=C["surface"]):
        super().__init__(master, 80, height, bg)
        self._h = height
        self._ratio = 0.0
        self._color = C["accent"]

    def set(self, ratio: float, color: str):
        self._ratio = max(0.0, min(1.0, ratio))
        self._color = color
        self._redraw()

    def _paint(self, p, w, h):
        r = h / 2
        p.rrect(0, 0, w, h, r, C["track"])
        rw = max(h, w * self._ratio)
        if self._ratio > 0:
            p.rrect(0, 0, min(rw, w), h, r, self._color)


class Tag(AaCanvas):
    """小标签（如「云机房」「CDN」）。"""

    def __init__(self, master, text, color, bg, fonts):
        f = fonts["tiny"]
        w = tkfont.Font(font=f).measure(text) + 14
        super().__init__(master, w, 18, bg)
        self._text = text
        self._color = color
        self._font = f

    def _paint(self, p, w, h):
        p.rrect(0, 0, w, h, 4, self._color)

    def _paint_text(self, w, h):
        self.create_text(w / 2, h / 2, text=self._text, fill="#FFFFFF",
                         font=self._font)


class Dot(AaCanvas):
    """小圆点（地区配色样本）。"""

    def __init__(self, master, color, size=8, bg=C["surface"]):
        super().__init__(master, size, size, bg)
        self._color = color
        self._size = size

    def _paint(self, p, w, h):
        # Pillow 的 ellipse 有抗锯齿，直接画满整块
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2
        if p.d is not None:
            k = p.k
            p.d.ellipse([(cx - r) * k, (cy - r) * k,
                         (cx + r) * k, (cy + r) * k],
                        fill=_rgb(self._color))
        else:
            p.tkoff.create_oval(0, 0, w * p.k, h * p.k,
                                fill=self._color, outline="")


# ---------------------------------------------------------------- 连接行


class ConnRow:
    """一条连接在表格里的行。

    用固定高度的 Frame + place 定位单元格。行高必须显式设定：
    Frame 里全是 place 的子控件时不会有自然高度，pack 后会塌成 0。
    """

    ROW_H = 30
    ICON = 16

    def __init__(self, parent, conn: Connection, fonts, widths, index: int,
                 on_click=None, on_context=None, pad=10):
        self.conn = conn
        self.fonts = fonts
        self.selected = False
        self.on_click = on_click
        self.on_context = on_context
        self.index = index
        self._has_color = False
        self._icon = None
        # 记下左侧内边距：兜底图标要与真图标严格同位，不能靠 winfo_x()
        # 反查（构造期控件还没布局，读回来是 0）。
        self._pad = pad
        self.frame = tk.Frame(parent, bg=C["surface"], height=self.ROW_H)
        self.frame.pack(fill="x")
        self.frame.pack_propagate(False)

        geo = conn.geo or {}
        country = geo.get("country") or ""
        color = region_color(country, index) if country else C["text_muted"]

        # 进程列：图标 + 名字。图标拿不到就用首字母色块兜底。
        proc_w = widths["proc"]
        self.icon_lbl = tk.Label(self.frame, bg=C["surface"], bd=0,
                                 takefocus=0)
        self.icon_lbl.place(x=pad, y=(self.ROW_H - self.ICON) // 2,
                            width=self.ICON, height=self.ICON)

        self.cells = []
        # 第一列（应用/进程）特殊：先让出图标位，文字再往后排。
        # 其余列一律从「该列起点」开始，起点按 widths 累加 —— 必须和表头
        # 的累加方式完全一致，否则列会逐列漂移，越往右错得越多。
        cur = pad
        icon_end = pad + self.ICON + 6          # 图标右侧、文字起点
        values = [
            (conn.scan_proc_display(), proc_w - self.ICON - 6, "w",
             C["text"], "small", icon_end),
            (conn.scan_state_display(), widths["state"], "w",
             conn.scan_state_color(), "small", None),
            # IP / 端口 / PID 用 8pt 等宽：9pt 下一整条 IPv4 要 165px，
            # 全表宽度撑不住；降到 8pt 是 135px，正好放得下且不横向滚动。
            (conn.remote_ip, widths["ip"], "w", C["text"], "mono_sm", None),
            (str(conn.remote_port), widths["port"], "w",
             C["text_sub"], "mono_sm", None),
            (str(conn.pid), widths["pid"], "w",
             C["text_muted"], "mono_sm", None),
            (geo.get("province") or "—", widths["province"], "w",
             color, "small", None),
            (conn.scan_city_display(), widths["city"], "w",
             C["text_sub"], "small", None),
            (conn.scan_district_display(), widths["district"], "w",
             C["text_muted"], "small", None),
            (conn.scan_network_display(), widths["net"], "w",
             C["text_muted"], "small", None),
        ]
        for i, (text, w, anchor, fg, fkey, fixed_x) in enumerate(values):
            x = fixed_x if fixed_x is not None else cur
            # 单元格实际可用宽要和 place 时一致（w - 8），否则算出来的
            # 截断位置会比显示区多出 8px，末尾那截又变成硬切。
            cell_w = max(20, w - 8)
            # 尾列（运营商/机房）数据源是外部字符串，长度不可控。
            # 用 fit_net_text 按像素截，并**优先保住末尾的 [CDN]/[云机房] 标签** ——
            # 那才是这列真正有信息量的部分，不能被名字挤掉。
            if i == len(values) - 1:
                text = fit_net_text(text, _font_of(self.frame, fonts[fkey]),
                                    cell_w, "…")
            lbl = tk.Label(self.frame, text=text, anchor=anchor,
                           bg=C["surface"], fg=fg, font=fonts[fkey],
                           justify="left")
            # 高度写死，保证整行对齐
            lbl.place(x=x, y=0, width=cell_w, height=self.ROW_H)
            # 每列都按该列的标称宽度前进，和表头严格对齐
            cur += (proc_w if i == 0 else w)
            self.cells.append(lbl)

        self._apply_icon()

        for widget in [self.frame, self.icon_lbl] + self.cells:
            widget.bind("<Button-1>", self._click)
            widget.bind("<Button-3>", self._context)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    # -- 图标 -------------------------------------------------------

    def _apply_icon(self):
        """给进程列取图标。没图标就画一个首字母色块。"""
        path = self.conn.proc_path
        rgba = winproc.extract_icon_rgba(path, self.ICON) if path else None
        if rgba:
            photo = winproc.icon_to_photo(rgba, self.ICON, master=self.frame)
            if photo is not None:
                self._icon = photo
                self.icon_lbl.configure(image=photo, bg=C["surface"])
                return
        self._fallback_icon()

    def _fallback_icon(self):
        """没有图标资源时，用进程名首字母 + 稳定配色画个方块。

        比一个空白占位符更容易辨认，也不会让行看起来缺了一块。
        """
        name = human_proc_name(self.conn.proc_name) or "?"
        letter = name.lstrip("(")[:1].upper() or "?"
        color = region_color("", hash(self.conn.proc_name) % 10)
        # 用 Canvas 画，避免依赖字体是否能渲染某些字符
        cv = tk.Canvas(self.frame, width=self.ICON, height=self.ICON,
                       highlightthickness=0, bd=0, bg=C["surface"])
        # 位置必须用构造时那个固定的 pad 值。
        # 不能用 self.icon_lbl.winfo_x() —— 此刻还在 __init__ 里、
        # 控件尚未布局完成，读回来恒为 0，兜底图标就会比真图标左移 10px，
        # 同一列里两种图标错开，看着就是「图标没对齐」。
        cv.place(x=self._pad, y=(self.ROW_H - self.ICON) // 2,
                 width=self.ICON, height=self.ICON)
        p = SsPainter(self.ICON, self.ICON, C["surface"])
        try:
            p.rrect(0, 0, self.ICON - 1, self.ICON - 1, 4, color)
        finally:
            p.release()
        img = p.to_photo(cv)
        if img is not None:
            self._icon = img
            cv.create_image(0, 0, image=img, anchor="nw")
        cv.create_text(self.ICON / 2, self.ICON / 2, text=letter,
                       fill="#FFFFFF",
                       font=(self.fonts.get("_family", FONT_FAMILY), 8, "bold"))
        # 用 Canvas 替换掉原先的 Label
        self.icon_lbl.destroy()
        self.icon_lbl = cv
        for widget in [cv]:
            widget.bind("<Button-1>", self._click)
            widget.bind("<Button-3>", self._context)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)

    # -- 交互 -------------------------------------------------------

    def _widgets(self):
        return [self.frame, self.icon_lbl] + self.cells

    def _click(self, _e=None):
        if self.on_click:
            self.on_click(self)

    def _context(self, event):
        if self.on_context:
            self.on_context(self, event)

    def _enter(self, _e=None):
        if self.selected:
            return
        for w in self._widgets():
            try:
                w.configure(bg=C["row_hover"])
            except tk.TclError:
                pass

    def _leave(self, _e=None):
        bg = C["accent_soft"] if self.selected else C["surface"]
        for w in self._widgets():
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def set_selected(self, flag: bool):
        self.selected = flag
        bg = C["accent_soft"] if flag else C["surface"]
        for w in self._widgets():
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def destroy(self):
        self.frame.destroy()

    def refresh_geo(self):
        """地理结果到达后，只更新受影响的单元格文字。

        不重建整行——重建会让表格重排、正在看的位置跳走。
        单元格下标：0 进程 1 状态 2 IP 3 端口 4 PID 5 省 6 市 7 区 8 运营商
        """
        geo = self.conn.geo or {}
        country = geo.get("country") or ""
        if country and not self._has_color:
            self._has_color = True
            self.cells[5].configure(fg=region_color(country, self.index))
        self.cells[5].configure(text=geo.get("province") or "—")
        self.cells[6].configure(text=self.conn.scan_city_display())
        self.cells[7].configure(text=self.conn.scan_district_display())
        self.cells[8].configure(text=self.conn.scan_network_display())


# ---------------------------------------------------------------- 主程序


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.conns: list = []
        self.filtered: list = []
        self.rows: list = []
        self.selected_row = None
        self.worker = None
        self.geo_worker = None
        self.stop_flag = False
        self.geo_progress = (0, 0)
        self.scan_started = 0.0
        self.scan_cost = 0.0
        self.last_error = ""
        self.filter_kind = "public"      # public | lan
        self.filter_country = None       # None = 全部
        self.search_text = ""
        self.group_mode = "region"       # region | app

        self.show_resolved_var = tk.BooleanVar(value=False)
        self.auto_geo_var = tk.BooleanVar(value=True)

        self.setup_fonts()
        self.setup_window()
        self.apply_window_icon()
        self.build_ui()

        # 工作线程与界面之间只通过队列通信。
        # 不用 root.after()：窗口还没进 mainloop（或由测试脚本手动 update()）时，
        # 从子线程调用 after 会抛 "main thread is not in main loop"。
        self.events = queue.Queue()
        self._pump_scheduled = False
        self.root.after(120, self.scan)
        self.root.after(60, self.pump_events)

    # ------------------------------------------------------------ 初始化

    def setup_fonts(self):
        fam = pick_font(self.root, ["Microsoft YaHei UI", "Microsoft YaHei",
                                    "PingFang SC", "Noto Sans CJK SC"],
                        FONT_FAMILY)
        mono = pick_font(self.root, ["Cascadia Mono", "Consolas",
                                     "DejaVu Sans Mono"], MONO_FAMILY)
        self.family = fam
        self.mono_family = mono
        self.fonts = {
            "h1":     (fam, 19, "bold"),
            "h2":     (fam, 13, "bold"),
            "h3":     (fam, 11, "bold"),
            "body":   (fam, 11),
            "small":  (fam, 9),
            "tiny":   (fam, 8),
            "mono":   (mono, 9),
            "mono_sm": (mono, 8),
        }

    def setup_window(self):
        self.root.title(f"{APP_NAME} · ConnMap")
        self.root.configure(bg=C["bg"])
        w, h = 1360, 860
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(w, max(1080, sw - 60))
        h = min(h, max(680, sh - 100))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.minsize(1080, 680)

    def apply_window_icon(self):
        self._icon_img = None
        for name in ("icon.png", "icon.ico"):
            p = os.path.join(HERE, name)
            if not os.path.exists(p):
                continue
            try:
                if name.endswith(".png"):
                    self._icon_img = tk.PhotoImage(file=p)
                    self.root.iconphoto(True, self._icon_img)
                else:
                    self.root.iconbitmap(p)
                return
            except Exception:  # noqa: BLE001
                continue
        self._draw_fallback_icon()

    def _draw_fallback_icon(self):
        try:
            img = tk.PhotoImage(width=32, height=32)
            img.put(C["accent"], to=(0, 0, 32, 32))
            self._icon_img = img
            self.root.iconphoto(True, img)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 界面搭建

    def build_ui(self):
        self.build_header()
        wrap = tk.Frame(self.root, bg=C["bg"])
        wrap.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        self.build_toolbar(wrap)
        self.build_body(wrap)
        self.build_footer()

    def build_header(self):
        head = tk.Frame(self.root, bg=C["bg"])
        head.pack(fill="x", padx=18, pady=(14, 10))

        left = tk.Frame(head, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text=APP_NAME, bg=C["bg"], fg=C["text"],
                 font=self.fonts["h1"]).pack(anchor="w")
        tk.Label(left, text=APP_SUBTITLE, bg=C["bg"], fg=C["text_muted"],
                 font=self.fonts["small"]).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(head, bg=C["bg"])
        right.pack(side="right")
        self.btn_scan = FlatButton(right, "重新扫描", self.scan, primary=True,
                                   btn_width=104, btn_height=36,
                                   font=self.fonts["small"], bg=C["bg"])
        self.btn_scan.pack(side="right")
        self.btn_copy = FlatButton(right, "复制报告", self.copy_report,
                                   btn_width=92, btn_height=36,
                                   font=self.fonts["small"], bg=C["bg"])
        self.btn_copy.pack(side="right", padx=(0, 8))

    def build_toolbar(self, parent):
        card = RoundedFrame(parent, radius=10, fill=C["surface"],
                            outline=C["border"], height=104, bg=C["bg"])
        card.pack(fill="x")
        card.pack_propagate(False)
        box = card.body

        # 第一行：筛选
        r1 = tk.Frame(box, bg=C["surface"])
        r1.pack(fill="x", padx=14, pady=(10, 0))

        tk.Label(r1, text="范围", bg=C["surface"], fg=C["text_sub"],
                 font=self.fonts["small"]).pack(side="left", padx=(0, 8))
        self.kind_chips = []
        for key, label in (("public", "仅公网连接"), ("lan", "含局域网")):
            ch = Chip(r1, label, lambda k=key: self.set_kind(k), self.fonts,
                      active=(key == "public"), bg=C["surface"])
            ch.pack(side="left", padx=(0, 6))
            ch.kind_key = key
            self.kind_chips.append(ch)

        tk.Frame(r1, bg=C["border"], width=1, height=18).pack(
            side="left", padx=10)
        tk.Label(r1, text="搜索", bg=C["surface"], fg=C["text_sub"],
                 font=self.fonts["small"]).pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        self.entry = tk.Entry(r1, textvariable=self.search_var,
                              font=self.fonts["small"], bd=0,
                              highlightthickness=1, highlightbackground=C["border"],
                              highlightcolor=C["accent"], bg=C["surface"],
                              fg=C["text"], insertbackground=C["text"])
        self.entry.pack(side="left", ipady=4, ipadx=6)
        self.entry.configure(width=22)
        self.search_var.trace_add("write", lambda *a: self.apply_filter())

        CheckBox(r1, "只看已定位", self.show_resolved_var, self.fonts,
                 bg=C["surface"]).pack(side="left", padx=(14, 0))
        CheckBox(r1, "自动查地理位置", self.auto_geo_var, self.fonts,
                 bg=C["surface"]).pack(side="left", padx=(10, 0))

        # 第二行：统计摘要
        r2 = tk.Frame(box, bg=C["surface"])
        r2.pack(fill="x", padx=14, pady=(8, 10))
        self.stats_lbl = tk.Label(r2, text="准备扫描…", bg=C["surface"],
                                  fg=C["text_sub"], font=self.fonts["small"])
        self.stats_lbl.pack(side="left")
        self.geo_lbl = tk.Label(r2, text="", bg=C["surface"],
                                fg=C["text_muted"], font=self.fonts["small"])
        self.geo_lbl.pack(side="left", padx=(14, 0))

        self.btn_geo = FlatButton(r2, "查询地理位置", self.run_geo,
                                  btn_width=110, btn_height=28,
                                  font=self.fonts["tiny"], bg=C["surface"])
        self.btn_geo.pack(side="right")

    def build_body(self, parent):
        body = tk.Frame(parent, bg=C["bg"])
        body.pack(fill="both", expand=True, pady=(10, 0))

        # 左侧：汇总
        self.left = tk.Frame(body, bg=C["bg"], width=316)
        self.left.pack(side="left", fill="y")
        self.left.pack_propagate(False)

        # 右侧：连接表
        self.right = tk.Frame(body, bg=C["bg"])
        self.right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self.build_summary_panel()
        self.build_table_panel()

    def build_summary_panel(self):
        for child in self.left.winfo_children():
            child.destroy()

        # 概览卡
        # 高度要装下：标题(约 26) + 环体 76 + 副标题 18 + 上下留白，
        # 给 168 才有余量；卡里三行统计也才够竖着排开不被裁。
        self.card_overview = RoundedFrame(self.left, radius=10, fill=C["surface"],
                                          outline=C["border"], height=168,
                                          bg=C["bg"])
        self.card_overview.pack(fill="x")
        self.card_overview.pack_propagate(False)
        ov = self.card_overview.body

        tk.Label(ov, text="扫描概览", bg=C["surface"], fg=C["text"],
                 font=self.fonts["h3"]).pack(anchor="w", padx=14, pady=(10, 8))

        row = tk.Frame(ov, bg=C["surface"])
        row.pack(fill="x", padx=14)
        # 环体 76px + 副标题带 18px。环内只放百分比，副标题在环下沿外边，
        # 这样「100%」不会被环壁挤扁。
        self.ring = Ring(row, size=76, sub_h=18, bg=C["surface"])
        self.ring.pack(side="left", anchor="n")
        self.ov_stats = tk.Frame(row, bg=C["surface"])
        self.ov_stats.pack(side="left", padx=(14, 0), fill="both", expand=True)
        self._ov_labels = {}
        for key, label in (("apps", "个应用"), ("ips", "个服务器 IP"),
                           ("countries", "个国家/地区")):
            line = tk.Frame(self.ov_stats, bg=C["surface"])
            line.pack(fill="x", pady=3)
            v = tk.Label(line, text="0", bg=C["surface"], fg=C["text"],
                         font=(self.family, 13, "bold"))
            v.pack(side="left")
            tk.Label(line, text=" " + label, bg=C["surface"],
                     fg=C["text_muted"], font=self.fonts["tiny"]).pack(side="left")
            self._ov_labels[key] = v

        # 分组切换
        self.card_group = RoundedFrame(self.left, radius=10, fill=C["surface"],
                                       outline=C["border"], height=44, bg=C["bg"])
        self.card_group.pack(fill="x", pady=(10, 0))
        self.card_group.pack_propagate(False)
        gbox = self.card_group.body
        self.group_chips = []
        for key, label in (("region", "按地区"), ("app", "按应用")):
            ch = Chip(gbox, label, lambda k=key: self.set_group(k), self.fonts,
                      active=(key == "region"), bg=C["surface"])
            ch.pack(side="left", padx=(12, 6), pady=8)
            ch.group_key = key
            self.group_chips.append(ch)
        tk.Label(gbox, text="分布", bg=C["surface"], fg=C["text_muted"],
                 font=self.fonts["tiny"]).pack(side="left", padx=(4, 0))

        # 汇总列表（可滚动）
        self.card_list = RoundedFrame(self.left, radius=10, fill=C["surface"],
                                      outline=C["border"], bg=C["bg"])
        self.card_list.pack(fill="both", expand=True, pady=(10, 0))
        lbox = self.card_list.body

        hdr = tk.Frame(lbox, bg=C["surface"])
        hdr.pack(fill="x", padx=12, pady=(10, 4))
        self.sum_title = tk.Label(hdr, text="地区分布", bg=C["surface"],
                                  fg=C["text"], font=self.fonts["h3"])
        self.sum_title.pack(side="left")
        self.btn_clear_filter = tk.Label(hdr, text="清除筛选", bg=C["surface"],
                                         fg=C["accent"], font=self.fonts["tiny"],
                                         cursor="hand2")
        self.btn_clear_filter.bind("<Button-1>", lambda e: self.clear_filter())

        cv = tk.Canvas(lbox, bg=C["surface"], highlightthickness=0, bd=0)
        sb = tk.Scrollbar(lbox, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=(0, 8))
        cv.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        self.sum_canvas = cv
        self.sum_inner = tk.Frame(cv, bg=C["surface"])
        self._sum_win = cv.create_window((0, 0), window=self.sum_inner,
                                         anchor="nw")
        self.sum_inner.bind(
            "<Configure>",
            lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>",
                lambda e: cv.itemconfigure(self._sum_win, width=e.width))
        cv.bind("<MouseWheel>",
                lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))

    def build_table_panel(self):
        card = RoundedFrame(self.right, radius=10, fill=C["surface"],
                            outline=C["border"], bg=C["bg"])
        card.pack(fill="both", expand=True)
        box = card.body

        head = tk.Frame(box, bg=C["surface"])
        head.pack(fill="x", padx=14, pady=(10, 6))
        self.tbl_title = tk.Label(head, text="连接明细", bg=C["surface"],
                                  fg=C["text"], font=self.fonts["h3"])
        self.tbl_title.pack(side="left")
        self.tbl_hint = tk.Label(head, text="", bg=C["surface"],
                                 fg=C["text_muted"], font=self.fonts["tiny"])
        self.tbl_hint.pack(side="left", padx=(10, 0))

        # 表头
        # 各列宽度必须按「最长真实取值 + 单元格内边距」反推，且必须用
        # tkfont.Font.measure() 在 **开过 DPI 感知** 的环境里量 ——
        # 不开 DPI 感知时屏幕报的是逻辑像素，量出来的字宽会明显偏小
        # （同一个 IP 曾经量到 98，实际要 165），据此定宽就会真的截断。
        # 样本也要从**真实数据源**取：别名表、地名映射表、状态映射表，
        # 不要手编「看着挺长」的字符串（会白占宽度、挤掉别的列）。
        #
        # 1360 窗口下表格可视区实测 950px。需求如下（px）：
        #   proc  图标 16 + 间距 6 + 最长别名「Google Chrome 浏览器」194
        #   state 「已连接/连接中/握手中」54（原始状态已被 collect 过滤或映射）
        #   ip    等宽 8pt 下最长 IP 135（9pt 要 165，装不下）
        #   pid   等宽 8pt 下 4294967295 = 90
        #   port  等宽 8pt 下 65535 = 45
        #   place 地名映射表最长「加利福尼亚 / 阿姆斯特丹」90
        #   net   外部字符串，长度不可控。用户定的是「标签优先、名字压短」，
        #         但太窄会让名字只剩一两个字（`H…　[云机房]`）等于没显示，
        #         所以给到 168（单元格 160）—— 名字能留 5~7 个字符：
        #         `Micros…　[云机房]`。代价是整表 989px > 可视 950px，
        #         最右侧要拖横向滚动条（滚动条与表头同步的逻辑本来就已就位）。
        #         这是用户明确选的取舍：宁可滚动，也要看清名字+标签。
        # 所以 ip/pid/port 三列用 mono_sm(8pt)，其余 9pt。
        # 省/市/区 三列要装得下「规范化后」的真实最长值：
        #   省 → 不列颠哥伦比亚（126px，加拿大卑诗省 IP 真会返回）
        #   市 → 阿姆斯特丹（90px，荷兰 IP 真会返回）
        #   区 → 乌鲁木齐（72px，国内经 PConline 补齐的区县）
        # 故 province=138 / city=104 / district=84（各留 ~4~6px 余量）。
        # 整表 1105 + 10 = 1115px，超出 950px 可视区 → 需横向滚动（用户已确认可接受）。
        self.widths = {
            "proc": 234, "state": 66, "ip": 152, "port": 57,
            "pid": 102, "province": 138, "city": 104, "district": 84, "net": 168,
        }
        self.table_w = sum(self.widths.values()) + 10

        self.header = tk.Canvas(box, height=28, bg=C["surface_alt"],
                                highlightthickness=0, bd=0)
        self.header.pack(fill="x", padx=14)
        self._draw_header()
        # 滚动区：横向 + 纵向都要能滚，列宽合计可能超过可见宽度
        cvwrap = tk.Frame(box, bg=C["surface"])
        cvwrap.pack(fill="both", expand=True, padx=(14, 0), pady=(0, 10))

        self.canvas = tk.Canvas(cvwrap, bg=C["surface"], highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(cvwrap, orient="vertical", command=self.canvas.yview)
        self._hsb = tk.Scrollbar(box, orient="horizontal",
                                 command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", padx=(0, 4))
        self.canvas.pack(side="left", fill="both", expand=True)
        self._hsb.pack(fill="x", padx=(14, 14))

        self.rows_inner = tk.Frame(self.canvas, bg=C["surface"])
        self._rows_win = self.canvas.create_window((0, 0), window=self.rows_inner,
                                                   anchor="nw")
        self.rows_inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(
                self._rows_win, width=max(e.width, self.table_w)))
        # 表头与表体横向同步，否则两边错位
        self.canvas.configure(xscrollcommand=self._on_xscroll)
        self.canvas.bind_all("<MouseWheel>", self.on_wheel)
        self.canvas.bind_all("<Shift-MouseWheel>",
                             lambda e: self.canvas.xview_scroll(
                                 int(-e.delta / 120), "units"))

        self.empty_lbl = tk.Label(self.rows_inner, text="", bg=C["surface"],
                                  fg=C["text_muted"], font=self.fonts["small"])

    def _on_xscroll(self, first, last):
        """表体横向滚动时，把表头一起挪。"""
        try:
            self._hsb.set(first, last)
            self.header.xview_moveto(first)
        except (AttributeError, tk.TclError):
            pass

    def _draw_header(self):
        """表头跟着横向滚动一起移动。"""
        self.header.delete("all")
        cols = [
            ("应用 / 进程", "proc"), ("状态", "state"), ("服务器 IP", "ip"),
            ("端口", "port"), ("PID", "pid"),
            ("省", "province"), ("市", "city"), ("区", "district"),
            ("运营商/机房", "net"),
        ]
        pad = 10
        x = pad
        for text, key in cols:
            self.header.create_text(
                x, 14, text=text, anchor="w", fill=C["text_sub"],
                font=self.fonts["tiny"])
            x += self.widths[key]
        self.header.configure(scrollregion=(0, 0, x, 28))

    def build_footer(self):
        foot = tk.Frame(self.root, bg=C["bg"])
        foot.pack(fill="x", padx=18, pady=(0, 10))
        self.status_lbl = tk.Label(
            foot, text="就绪", bg=C["bg"], fg=C["text_muted"],
            font=self.fonts["tiny"])
        self.status_lbl.pack(side="left")
        tk.Label(foot, text=f"{APP_NAME} v{APP_VERSION}　{AUTHOR}",
                 bg=C["bg"], fg=C["text_muted"],
                 font=self.fonts["tiny"]).pack(side="right")

    def on_wheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        p = widget
        while p is not None:
            if p is self.canvas:
                self.canvas.yview_scroll(int(-event.delta / 120), "units")
                break
            if p is getattr(self, "sum_canvas", None):
                self.sum_canvas.yview_scroll(int(-event.delta / 120), "units")
                break
            try:
                p = p.master
            except Exception:  # noqa: BLE001
                break

    # ------------------------------------------------------------ 扫描

    def scan(self):
        if self.worker is not None and self.worker.is_alive():
            return
        self.stop_flag = False
        self.conns = []
        self.filtered = []
        self.selected_row = None
        self.filter_country = None
        self.set_running(True)
        self.status_lbl.configure(text="正在枚举本机连接…")
        self.stats_lbl.configure(text="扫描中…")
        self.clear_rows()
        self.build_summary_panel()

        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def _worker(self):
        try:
            self.scan_started = time.time()
            kind = NET_PUBLIC if self.filter_kind == "public" else NET_LAN
            conns = connscan.collect(min_kind=kind, include_listening=False)
            self.scan_cost = time.time() - self.scan_started
            self.post("scan_done", conns)
        except Exception as exc:  # noqa: BLE001
            self.post("scan_failed", f"{type(exc).__name__}: {exc}")

    def _scan_failed(self, msg):
        self.last_error = msg
        self.set_running(False)
        self.status_lbl.configure(text=f"扫描失败：{msg}")
        self.stats_lbl.configure(text="扫描失败")

    def _scan_done(self, conns):
        self.conns = conns
        self.last_error = ""
        n_apps = len({(c.proc_name, c.pid) for c in conns})
        n_ips = len({c.remote_ip for c in conns})
        self.stats_lbl.configure(
            text=f"共 {len(conns)} 条连接　{n_apps} 个应用　{n_ips} 个服务器 IP"
                 f"　（{self.scan_cost:.1f} 秒）")
        self.status_lbl.configure(
            text=f"扫描完成，用时 {self.scan_cost:.1f} 秒；"
                 f"共 {len(conns)} 条连接 / {n_apps} 个应用 / {n_ips} 个服务器 IP")
        self.geo_lbl.configure(text="")
        # 先上表再查地理：查到什么立刻填进去，不攒到最后。
        # 所以这里不禁用按钮，geo 线程另起一个。
        self.btn_scan.set_enabled(True)
        self.apply_filter()
        self.update_summary()
        if self.auto_geo_var.get() and conns:
            self.root.after(60, self.run_geo)

    # ------------------------------------------------------------ 地理位置

    def run_geo(self):
        if not self.conns:
            return
        if self.geo_worker is not None and self.geo_worker.is_alive():
            return
        pending = [ip for ip in connscan.public_ip_set(self.conns)
                   if not (self._geo_of(ip) or {}).get("ok")]
        if not pending:
            self.status_lbl.configure(text="所有公网 IP 都已定位完成")
            self.geo_lbl.configure(
                text=f"已定位 {len(connscan.public_ip_set(self.conns))} 个 IP")
            return

        self.set_running(True, geo_only=True)
        self.geo_progress = (0, len(pending))
        self.geo_lbl.configure(text=f"0 / {len(pending)} 个 IP")
        self.status_lbl.configure(text=f"正在查询 {len(pending)} 个 IP 的地理位置…")

        self.geo_worker = threading.Thread(
            target=self._geo_worker, args=(pending, True), daemon=True)
        self.geo_worker.start()

    def _geo_worker(self, ips, use_cache):
        try:
            for done, total, ip, geo in geoloc.lookup_stream(
                    ips, use_cache=use_cache, workers=4,
                    should_stop=lambda: self.stop_flag):
                # 每个 IP 一查完就推给界面，边查边显示
                self.post("geo_tick", (done, total, ip, geo))
        except Exception as exc:  # noqa: BLE001
            self.post("geo_failed", f"{type(exc).__name__}: {exc}")
        self.post("geo_done", None)

    def _geo_tick(self, done, total, ip, geo):
        """一条 IP 的定位结果回来了：立刻回填 + 刷那一行。"""
        self.geo_lbl.configure(text=f"{done} / {total} 个 IP")
        self.geo_progress = (done, total)
        if total:
            self.ring.set_value(done / total, sub="定位")
        if geo:
            for c in self.conns:
                if c.remote_ip == ip:
                    c.geo = geo
        region = (geoloc.describe_region(geo)
                  if geo and geo.get("ok") else (geo or {}).get("error", "未定位"))
        self.status_lbl.configure(text=f"[{done}/{total}] {ip} → {region}")
        # 结果到了就更新对应行，同时让地区汇总实时长出来
        self.refresh_live()

    def _geo_failed(self, msg):
        self.set_running(False)
        self.status_lbl.configure(text=f"地理查询失败：{msg}")

    def _geo_done(self, _geos):
        self.set_running(False)
        ips = connscan.public_ip_set(self.conns)
        located = sum(1 for ip in ips if (self._geo_of(ip) or {}).get("ok"))
        total = len(ips)
        self.status_lbl.configure(
            text=f"地理位置查询完成：{located} / {total} 个公网 IP 已定位")
        # 进度标签统一按「已定位 IP / 公网 IP 总数」，不混入连接条数
        self.geo_lbl.configure(text=f"已定位 {located} / {total} 个 IP")
        self.refresh_live(full=True)

    def refresh_live(self, full: bool = False):
        """把已到的地理结果增量刷到表与汇总上。

        增量刷不能重排整张表（会把用户正在看的位置跳走），
        所以只更新那些已经拿到结果的行的单元格文字；
        只有收尾时（full=True）才重新排序与重建汇总。
        """
        if full:
            self.apply_filter()
            self.update_summary()
            return
        # 只更新文本，不动布局
        for row in self.rows:
            if row.conn.geo:
                row.refresh_geo()
        # 汇总面板是轻量重建，直接刷新即可；行不重排
        self.update_summary(full=False)

    def _geo_of(self, ip):
        for c in self.conns:
            if c.remote_ip == ip and c.geo:
                return c.geo
        return None

    def set_kind(self, key):
        if self.filter_kind == key:
            return
        self.filter_kind = key
        for ch in self.kind_chips:
            ch.active = (ch.kind_key == key)
            ch._redraw()
        self.scan()

    def set_group(self, key):
        self.group_mode = key
        for ch in self.group_chips:
            ch.active = (ch.group_key == key)
            ch._redraw()
        self.update_summary()

    def clear_filter(self):
        self.filter_country = None
        self.search_var.set("")
        self.apply_filter()
        self.update_summary()

    # ------------------------------------------------------------ 筛选与渲染

    def apply_filter(self):
        self.search_text = self.search_var.get().strip().lower()
        out = []
        for c in self.conns:
            geo = c.geo or {}
            if self.filter_country:
                if (geo.get("country") or "未知") != self.filter_country:
                    continue
            if self.show_resolved_var.get() and not geo.get("ok"):
                continue
            if self.search_text:
                blob = " ".join([
                    c.proc_name, human_proc_name(c.proc_name),
                    str(c.pid), c.remote_ip, str(c.remote_port),
                    geo.get("country", ""), geo.get("city", ""),
                    geo.get("datacenter", ""), geo.get("isp", ""),
                    geo.get("org", ""), c.service,
                ]).lower()
                if self.search_text not in blob:
                    continue
            out.append(c)

        # 排序：先按国家，再按应用，最后按 IP，让同类靠在一起
        def sort_key(c):
            geo = c.geo or {}
            return (
                geo.get("country") or "zzz",
                human_proc_name(c.proc_name).lower(),
                c.remote_ip,
                c.remote_port,
            )
        out.sort(key=sort_key)
        self.filtered = out
        self.render_rows()
        self.update_table_hint()

    def update_table_hint(self):
        bits = []
        if self.filter_country:
            bits.append(f"已筛选：{self.filter_country}")
        if self.search_text:
            bits.append(f"搜索「{self.search_var.get().strip()}」")
        bits.append(f"{len(self.filtered)} / {len(self.conns)} 条")
        if self.last_error:
            bits.append(f"错误：{self.last_error}")
        self.tbl_hint.configure(text="　".join(bits))

    def clear_rows(self):
        for r in self.rows:
            r.destroy()
        self.rows = []
        self.empty_lbl.pack_forget()

    def render_rows(self):
        self.clear_rows()
        # 重绘后把滚动位置归零，否则会停在上一轮的位置，首行被切掉
        try:
            self.canvas.yview_moveto(0)
            self.canvas.xview_moveto(0)
        except tk.TclError:
            pass
        if not self.filtered:
            self.empty_lbl.configure(
                text="没有符合条件的连接。\n"
                     "· 可能是当前没有程序在联外网\n"
                     "· 或者筛选条件太严，试试「清除筛选」")
            self.empty_lbl.pack(pady=40)
            return

        # 按国家分组的序号，保证同国家颜色一致
        country_index = {}
        for i, c in enumerate(self.filtered):
            country = (c.geo or {}).get("country") or ""
            if country and country not in country_index:
                country_index[country] = len(country_index)

        for c in self.filtered:
            country = (c.geo or {}).get("country") or ""
            row = ConnRow(self.rows_inner, c, self.fonts, self.widths,
                          country_index.get(country, 0), on_click=self.select_row,
                          on_context=self.show_row_menu)
            self.rows.append(row)

    # ------------------------------------------------------- 右键菜单

    def show_row_menu(self, row, event):
        """右键菜单：打开程序所在位置 / 复制路径 / 复制 PID。"""
        c = row.conn
        path = (c.proc_path or "").strip()
        m = tk.Menu(self.root, tearoff=0)
        if path:
            m.add_command(
                label="打开文件所在位置",
                command=lambda: self._reveal_path(path))
            m.add_command(
                label="打开程序",
                command=lambda: self._open_path(path))
            m.add_separator()
            m.add_command(
                label="复制程序路径",
                command=lambda: self._copy_text(path, "程序路径"))
        else:
            m.add_command(label="打开文件所在位置（无路径信息）", state="disabled")
            m.add_command(label="打开程序（无路径信息）", state="disabled")
            m.add_separator()
            m.add_command(label="复制程序路径（无路径信息）", state="disabled")

        m.add_command(
            label=f"复制进程名　{human_proc_name(c.proc_name)}",
            command=lambda: self._copy_text(c.proc_name, "进程名"))
        m.add_command(
            label=f"复制 PID　{c.pid}",
            command=lambda: self._copy_text(str(c.pid), "PID"))
        m.add_separator()
        m.add_command(
            label=f"复制远端地址　{c.remote_hostport}",
            command=lambda: self._copy_text(c.remote_hostport, "远端地址"))

        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _reveal_path(self, path):
        ok = winproc.reveal_in_explorer(path)
        self.status_lbl.configure(
            text=(f"已在资源管理器中定位：{path}" if ok
                  else f"定位失败（文件可能已不存在）：{path}"))

    def _open_path(self, path):
        ok = winproc.open_file(path)
        self.status_lbl.configure(
            text=(f"已打开：{path}" if ok
                  else f"打开失败（文件可能已不存在）：{path}"))

    def _copy_text(self, text, what):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_lbl.configure(text=f"已复制{what}：{text}")
        except tk.TclError as exc:
            self.status_lbl.configure(text=f"复制失败：{exc}")

    def select_row(self, row):
        if self.selected_row is row:
            row.set_selected(False)
            self.selected_row = None
        else:
            if self.selected_row is not None:
                self.selected_row.set_selected(False)
            row.set_selected(True)
            self.selected_row = row
        c = row.conn
        geo = c.geo or {}
        if geo.get("ok"):
            self.status_lbl.configure(
                text=f"{human_proc_name(c.proc_name)} → {c.remote_hostport}　"
                     f"{geoloc.describe_region(geo)}　{geoloc.describe_network(geo)}")
        else:
            self.status_lbl.configure(
                text=f"{human_proc_name(c.proc_name)} → {c.remote_hostport}"
                     f"　（尚未定位）")

    # ------------------------------------------------------------ 汇总

    def set_running(self, running, geo_only=False):
        self.btn_scan.set_enabled(not running)
        self.btn_copy.set_enabled(not running)
        if hasattr(self, "btn_geo"):
            self.btn_geo.set_enabled(not running)
        self.btn_scan.set_text("查询中…" if running else "重新扫描")

    def update_summary(self, full: bool = True):
        conns = self.conns
        if not conns:
            self.ring.set_value(0, label="0%", sub="")
            for v in self._ov_labels.values():
                v.configure(text="0")
            for child in self.sum_inner.winfo_children():
                child.destroy()
            return

        ips = connscan.public_ip_set(conns)
        located = sum(1 for ip in ips if (self._geo_of(ip) or {}).get("ok"))
        apps = {(c.proc_name, c.pid) for c in conns}
        countries = {
            (c.geo or {}).get("country")
            for c in conns if (c.geo or {}).get("country")
        }
        ratio = (located / len(ips)) if ips else 0.0
        self.ring.set_value(ratio, label=f"{int(ratio * 100)}%", sub="已定位")
        self._ov_labels["apps"].configure(text=str(len(apps)))
        self._ov_labels["ips"].configure(text=str(len(ips)))
        self._ov_labels["countries"].configure(text=str(len(countries)))

        self.render_summary()

    def render_summary(self):
        for child in self.sum_inner.winfo_children():
            child.destroy()

        if self.group_mode == "region":
            groups = {}
            for c in self.conns:
                geo = c.geo or {}
                country = geo.get("country") or "未知位置"
                groups.setdefault(country, []).append(c)
            items = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            self.sum_title.configure(text="地区分布")
            total = len(self.conns)
            for i, (country, items_c) in enumerate(items):
                self._summary_row(
                    label=country,
                    count=len(items_c),
                    total=total,
                    color=region_color(country, i),
                    sub=self._region_sub(items_c),
                    active=(self.filter_country == country),
                    on_click=lambda c=country: self._toggle_country(c),
                )
        else:
            groups = {}
            for c in self.conns:
                key = human_proc_name(c.proc_name)
                groups.setdefault(key, []).append(c)
            items = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            self.sum_title.configure(text="应用分布")
            total = len(self.conns)
            for i, (name, items_c) in enumerate(items):
                self._summary_row(
                    label=name,
                    count=len(items_c),
                    total=total,
                    color=REGION_COLORS[i % len(REGION_COLORS)],
                    sub=self._app_sub(items_c),
                    active=False,
                    on_click=None,
                )

    def _region_sub(self, conns) -> str:
        parts = []
        cities = {}
        dcs = set()
        for c in conns:
            geo = c.geo or {}
            if geo.get("city"):
                cities[geo["city"]] = cities.get(geo["city"], 0) + 1
            net = geoloc.short_network(geo)
            if net:
                dcs.add(net)
        top_cities = sorted(cities.items(), key=lambda kv: -kv[1])[:2]
        if top_cities:
            parts.append("、".join(k for k, _ in top_cities))
        if dcs:
            parts.append(next(iter(sorted(dcs)))[:22])
        return "　".join(parts) if parts else "未定位"

    def _app_sub(self, conns) -> str:
        countries = {}
        for c in conns:
            country = (c.geo or {}).get("country") or "未定位"
            countries[country] = countries.get(country, 0) + 1
        top = sorted(countries.items(), key=lambda kv: -kv[1])[:2]
        return "、".join(f"{k} {v}" for k, v in top) if top else ""

    def _summary_row(self, label, count, total, color, sub, active, on_click):
        wrap = tk.Frame(self.sum_inner, bg=C["surface_alt"] if active else C["surface"])
        wrap.pack(fill="x", padx=6, pady=2)

        top = tk.Frame(wrap, bg=wrap["bg"])
        top.pack(fill="x", padx=8, pady=(6, 2))
        dot = Dot(top, color, bg=wrap["bg"])
        dot.pack(side="left", pady=(4, 0))
        tk.Label(top, text=label, bg=wrap["bg"], fg=C["text"],
                 font=self.fonts["small"], anchor="w").pack(side="left", padx=(6, 0))
        tk.Label(top, text=str(count), bg=wrap["bg"], fg=C["text_sub"],
                 font=(self.family, 9, "bold")).pack(side="right")
        pct = tk.Label(top, text=f"{count * 100 // max(1, total)}%",
                       bg=wrap["bg"], fg=C["text_muted"], font=self.fonts["tiny"])
        pct.pack(side="right", padx=(0, 6))

        b = Bar(wrap, height=6, bg=wrap["bg"])
        b.pack(fill="x", padx=8, pady=(0, 2))
        b.set(count / max(1, total), color)

        if sub:
            tk.Label(wrap, text=sub, bg=wrap["bg"], fg=C["text_muted"],
                     font=self.fonts["tiny"], anchor="w").pack(
                fill="x", padx=8, pady=(0, 6))

        if on_click:
            widgets = [wrap, top] + list(top.winfo_children()) + [b]
            for w in widgets:
                w.bind("<Button-1>", lambda e, f=on_click: f())
                try:
                    w.configure(cursor="hand2")
                except tk.TclError:
                    pass

    def _toggle_country(self, country):
        if self.filter_country == country:
            self.filter_country = None
        else:
            self.filter_country = country
        self.apply_filter()
        self.render_summary()

    # ------------------------------------------------------------ 报告

    def build_report(self) -> str:
        conns = self.filtered or self.conns
        lines = []
        lines.append(f"{APP_NAME} 报告")
        lines.append("=" * 68)
        lines.append(f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"连接条数：{len(conns)}")
        ips = connscan.public_ip_set(conns)
        apps = {(c.proc_name, c.pid) for c in conns}
        lines.append(f"涉及应用：{len(apps)} 个")
        lines.append(f"服务器 IP：{len(ips)} 个")
        if self.last_error:
            lines.append(f"错误：{self.last_error}")
        lines.append("")

        # 按地区汇总
        by_country = {}
        for c in conns:
            country = (c.geo or {}).get("country") or "未定位"
            by_country.setdefault(country, []).append(c)
        lines.append("【地区分布】")
        for country, items in sorted(by_country.items(),
                                     key=lambda kv: -len(kv[1])):
            cities = sorted({(c.geo or {}).get("city", "") for c in items} - {""})
            nets = sorted({geoloc.short_network(c.geo or {}) for c in items} - {""})
            lines.append(f"  {country}　{len(items)} 条")
            if cities:
                lines.append(f"      城市：{'、'.join(cities[:6])}")
            if nets:
                lines.append(f"      机房：{'、'.join(nets[:4])}")
        lines.append("")

        # 按应用汇总
        lines.append("【应用分布】")
        by_app = {}
        for c in conns:
            by_app.setdefault(human_proc_name(c.proc_name), []).append(c)
        for name, items in sorted(by_app.items(), key=lambda kv: -len(kv[1])):
            regions = {}
            for c in items:
                r = geoloc.describe_location(c.geo or {})
                regions[r] = regions.get(r, 0) + 1
            top = "、".join(f"{k}({v})" for k, v in
                           sorted(regions.items(), key=lambda kv: -kv[1])[:4])
            lines.append(f"  {name}　{len(items)} 条　→ {top}")
        lines.append("")

        # 明细
        lines.append("【连接明细】")
        lines.append(f"{'应用':<22}{'服务器':<24}{'地区':<18}{'运营商/机房':<34}")
        lines.append("-" * 100)
        for c in sorted(conns, key=lambda x: (
                (x.geo or {}).get("country") or "zzz",
                human_proc_name(x.proc_name))):
            geo = c.geo or {}
            lines.append(
                f"{human_proc_name(c.proc_name)[:20]:<22}"
                f"{c.remote_hostport[:22]:<24}"
                f"{geoloc.describe_location(geo)[:16]:<18}"
                f"{geoloc.short_network(geo)[:32]:<34}")
        lines.append("")
        lines.append(f"— {APP_NAME} v{APP_VERSION} {AUTHOR}")
        return "\n".join(lines)

    def copy_report(self):
        text = self.build_report()
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self.status_lbl.configure(text="报告已复制到剪贴板")
        except tk.TclError as exc:
            self.status_lbl.configure(text=f"复制失败：{exc}")

    def post(self, kind, payload=None):
        """工作线程往界面线程投递事件。线程安全。"""
        self.events.put((kind, payload))

    def drain_events(self):
        """在界面线程里把队列排空。可被外部脚本反复调用。"""
        for _ in range(400):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            handler = getattr(self, "_ev_" + kind, None)
            if handler is None:
                continue
            try:
                if payload is None:
                    handler()
                else:
                    handler(payload)
            except tk.TclError:
                return

    def _ev_scan_done(self, conns):
        self._scan_done(conns)

    def _ev_scan_failed(self, msg):
        self._scan_failed(msg)

    def _ev_geo_tick(self, payload):
        done, total, ip, geo = payload
        self._geo_tick(done, total, ip, geo)

    def _ev_geo_done(self, geos=None):
        self._geo_done(geos)

    def _ev_geo_failed(self, msg):
        self._geo_failed(msg)

    def pump_events(self):
        """定时排空队列，让界面在 mainloop 里持续刷新。"""
        self.drain_events()
        try:
            self.root.after(80, self.pump_events)
        except tk.TclError:
            pass


# ---------------------------------------------------------------- 连接展示辅助
# 这些是给 Connection 加展示方法，避免在界面层散落格式化逻辑。


def _proc_display(self) -> str:
    name = human_proc_name(self.proc_name)
    return f"{name}"


def _state_display(self) -> str:
    return {
        "ESTABLISHED": "已连接",
        "SYN_SENT": "连接中",
        "SYN_RECV": "握手中",
        "LISTENING": "监听",
        "UDP": "UDP",
    }.get(self.state, self.state)


def _state_color(self) -> str:
    return {
        "ESTABLISHED": C["ok"],
        "SYN_SENT": C["warn"],
        "SYN_RECV": C["warn"],
        "LISTENING": C["text_muted"],
    }.get(self.state, C["text_muted"])


def _province_display(self) -> str:
    geo = self.geo or {}
    if not geo:
        return "查询中…"
    return geo.get("province") or "—"


def _city_display(self) -> str:
    geo = self.geo or {}
    if not geo:
        return "查询中…"
    return geo.get("city") or "—"


def _district_display(self) -> str:
    geo = self.geo or {}
    if not geo:
        return "查询中…"
    return geo.get("district") or "—"


def _network_display(self) -> str:
    geo = self.geo or {}
    if not geo:
        # 还没查到，明确区别于「查不到」——否则用户会以为是没结果
        return "查询中…"
    if not geo.get("ok"):
        return f"未定位（{str(geo.get('error') or '')[:22]}）"
    net = geoloc.short_network(geo)
    tags = []
    if geo.get("is_cdn"):
        tags.append("CDN")
    elif geo.get("is_cloud"):
        tags.append("云机房")
    if tags:
        return f"{net}　[{'/'.join(tags)}]" if net else "、".join(tags)
    # 这里**不要**再按字符数截 —— 单元格渲染时会用 fit_text 按像素宽
    # 补省略号。两处都截会导致「先被砍一半、再补省略号」，中间那段白丢。
    return net or "—"


Connection.scan_proc_display = _proc_display
Connection.scan_state_display = _state_display
Connection.scan_state_color = _state_color
Connection.scan_province_display = _province_display
Connection.scan_city_display = _city_display
Connection.scan_district_display = _district_display
Connection.scan_network_display = _network_display


# ---------------------------------------------------------------- 入口


def _write_crash_log(exc: BaseException) -> str:
    import traceback
    p = os.path.join(HERE, "connmap_crash.log")
    try:
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
    except OSError:
        pass
    return p


def main():
    root = tk.Tk()
    try:
        App(root)
    except Exception as exc:  # noqa: BLE001
        log = _write_crash_log(exc)
        try:
            messagebox.showerror(APP_NAME, f"启动失败：{exc}\n\n详情见：{log}")
        except tk.TclError:
            pass
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
