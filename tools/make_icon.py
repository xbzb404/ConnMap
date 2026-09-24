"""生成应用图标：一张「地球 + 连线到各地」的图形，呼应「连接地图」。

输出 icon.ico（多尺寸）与 icon.png，供打包与界面标题栏使用。
"""
import os

from PIL import Image, ImageDraw

ACCENT = (43, 127, 255)
ACCENT_DK = (22, 93, 200)
WARN = (255, 125, 0)
FAIL = (245, 63, 63)
OK = (0, 180, 42)
WHITE = (255, 255, 255)

HERE = os.path.dirname(os.path.abspath(__file__))   # 本脚本所在目录（tools/）
ROOT = os.path.dirname(HERE)                        # 项目根
SIZE = 512
SS = 4


def make_icon() -> Image.Image:
    s = SIZE * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 背景：圆角方形 + 竖向渐变
    radius = int(s * 0.22)
    for i in range(s):
        t = i / s
        r = int(ACCENT[0] * (1 - t) + ACCENT_DK[0] * t)
        g = int(ACCENT[1] * (1 - t) + ACCENT_DK[1] * t)
        b = int(ACCENT[2] * (1 - t) + ACCENT_DK[2] * t)
        d.line([(0, i), (s, i)], fill=(r, g, b, 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1],
                                           radius=radius, fill=255)
    img.putalpha(mask)

    d = ImageDraw.Draw(img)
    cx, cy = s * 0.42, s * 0.50

    # 地球：实心圆 + 经纬线，表示「地理位置」
    gr = s * 0.235
    d.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=(255, 255, 255, 255))

    # 纬线（横椭圆）
    for frac in (-0.55, 0.0, 0.55):
        ry = gr * abs(frac)
        line_w = max(2, int(s * 0.011))
        if ry < 2:
            d.line([(cx - gr, cy), (cx + gr, cy)], fill=ACCENT, width=line_w)
        else:
            d.ellipse([cx - gr, cy - ry, cx + gr, cy + ry],
                      outline=ACCENT, width=line_w)
    # 经线（竖椭圆）
    for frac in (-0.6, 0.0, 0.6):
        rx = gr * abs(frac)
        line_w = max(2, int(s * 0.011))
        if rx < 2:
            d.line([(cx, cy - gr), (cx, cy + gr)], fill=ACCENT, width=line_w)
        else:
            d.ellipse([cx - rx, cy - gr, cx + rx, cy + gr],
                      outline=ACCENT, width=line_w)

    # 右上：一个「远端节点」，用连线接到地球，表示「连到了别的国家」
    nr = s * 0.072
    node = (s * 0.795, s * 0.255)
    lw = max(3, int(s * 0.020))
    d.line([(cx + gr * 0.72, cy - gr * 0.72), node],
           fill=(255, 255, 255, 235), width=lw)
    d.ellipse([node[0] - nr, node[1] - nr, node[0] + nr, node[1] + nr],
              fill=FAIL)

    # 右下：第二个远端节点（不同颜色，暗示「不同地区」）
    node2 = (s * 0.815, s * 0.735)
    d.line([(cx + gr * 0.68, cy + gr * 0.74), node2],
           fill=(255, 255, 255, 235), width=lw)
    d.ellipse([node2[0] - nr, node2[1] - nr, node2[0] + nr, node2[1] + nr],
              fill=WARN)

    # 左下：第三个节点
    node3 = (s * 0.215, s * 0.815)
    d.line([(cx - gr * 0.66, cy + gr * 0.76), node3],
           fill=(255, 255, 255, 235), width=lw)
    d.ellipse([node3[0] - nr, node3[1] - nr, node3[0] + nr, node3[1] + nr],
              fill=OK)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    img = make_icon()
    png = os.path.join(ROOT, "icon.png")
    img.save(png)
    ico = os.path.join(ROOT, "icon.ico")
    img.save(ico, sizes=[(256, 256), (128, 128), (64, 64),
                         (48, 48), (32, 32), (16, 16)])
    print("已生成:", png)
    print("已生成:", ico)
    for f in (png, ico):
        print(f"  {os.path.basename(f)}  {os.path.getsize(f):,} 字节")


if __name__ == "__main__":
    main()
