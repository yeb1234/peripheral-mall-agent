"""生成每个 SKU 对应的产品图（SVG），写入 backend/static/products/{sku}.svg。

设计目标：
- 浅色 VAXEE 风格，干净专业，不使用真实照片（演示数据）。
- 每个 SKU 一张独立图片，文件名 = SKU，与后端 DB 的 image 字段一一对应，
  前端 ProductCard / ProductDetail / Home 新品插页直接读 `product.image` 即可。
- 品类用不同 glyph 区分（鼠标 / 键盘 / 耳机 / 摄像头 / 扩展坞 / 手柄）。

运行：/tmp/p4venv/Scripts/python.exe tools/gen_product_images.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.seed import PRODUCTS  # 复用同一份数据，保证与 DB 完全一致

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "products")

# 品类 -> 主色（浅色卡片上的点缀）
ACCENT = {
    "mouse": "#2f6df6",
    "keyboard": "#7c5cff",
    "headset": "#11b3a3",
    "webcam": "#f0883e",
    "dock": "#e0567a",
    "gamepad": "#3aa655",
}


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _wrap(text: str, max_chars: int = 14) -> list[str]:
    """简单按字符宽度折行（中文按字计）。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines[:3]


def _mouse(cx: float, cy: float, color: str) -> str:
    return f"""
    <rect x="{cx-70}" y="{cy-150}" width="140" height="300" rx="70" fill="#fff" stroke="{color}" stroke-width="6"/>
    <line x1="{cx}" y1="{cy-150}" x2="{cx}" y2="{cy-30}" stroke="{color}" stroke-width="5"/>
    <rect x="{cx-9}" y="{cy-120}" width="18" height="46" rx="9" fill="{color}"/>
    <circle cx="{cx}" cy="{cy+70}" r="10" fill="{color}" opacity="0.5"/>
    """


def _keyboard(cx: float, cy: float, color: str) -> str:
    x0, y0, w, h = cx - 180, cy - 70, 360, 140
    parts = [f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="14" fill="#fff" stroke="{color}" stroke-width="6"/>']
    kw, kh, gap = 38, 38, 8
    cols = 8
    for r in range(3):
        for c in range(cols):
            kx = x0 + 16 + c * (kw + gap)
            ky = y0 + 16 + r * (kh + gap)
            parts.append(f'<rect x="{kx:.0f}" y="{ky:.0f}" width="{kw}" height="{kh}" rx="7" fill="{color}" opacity="0.85"/>')
    # 空格行
    parts.append(f'<rect x="{x0+16}" y="{y0+16+3*(kh+gap):.0f}" width="{w-32}" height="{kh}" rx="7" fill="{color}" opacity="0.4"/>')
    return "\n".join(parts)


def _headset(cx: float, cy: float, color: str) -> str:
    return f"""
    <path d="M {cx-130} {cy+10} A 130 130 0 0 1 {cx+130} {cy+10}" fill="none" stroke="{color}" stroke-width="8"/>
    <rect x="{cx-160}" y="{cy-10}" width="58" height="110" rx="18" fill="#fff" stroke="{color}" stroke-width="6"/>
    <rect x="{cx+102}" y="{cy-10}" width="58" height="110" rx="18" fill="#fff" stroke="{color}" stroke-width="6"/>
    <circle cx="{cx-131}" cy="{cy+45}" r="20" fill="{color}" opacity="0.25"/>
    <circle cx="{cx+131}" cy="{cy+45}" r="20" fill="{color}" opacity="0.25"/>
    <path d="M {cx+160} {cy+90} q 30 10 26 50" fill="none" stroke="{color}" stroke-width="6" stroke-linecap="round"/>
    """


def _webcam(cx: float, cy: float, color: str) -> str:
    return f"""
    <rect x="{cx-100}" y="{cy-80}" width="200" height="150" rx="20" fill="#fff" stroke="{color}" stroke-width="6"/>
    <circle cx="{cx}" cy="{cy-5}" r="52" fill="#fff" stroke="{color}" stroke-width="6"/>
    <circle cx="{cx}" cy="{cy-5}" r="30" fill="{color}" opacity="0.85"/>
    <circle cx="{cx-10}" cy="{cy-15}" r="8" fill="#fff" opacity="0.8"/>
    <rect x="{cx-30}" y="{cy+85}" width="60" height="14" rx="7" fill="{color}" opacity="0.5"/>
    """


def _dock(cx: float, cy: float, color: str) -> str:
    x0, y0, w, h = cx - 170, cy - 60, 340, 120
    parts = [f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" rx="16" fill="#fff" stroke="{color}" stroke-width="6"/>']
    for i in range(6):
        px = x0 + 30 + i * 50
        parts.append(f'<rect x="{px}" y="{y0+24}" width="34" height="20" rx="5" fill="{color}" opacity="0.7"/>')
        parts.append(f'<rect x="{px}" y="{y0+58}" width="34" height="20" rx="5" fill="{color}" opacity="0.4"/>')
    parts.append(f'<circle cx="{x0+w-26}" cy="{y0+30}" r="8" fill="{color}"/>')
    return "\n".join(parts)


def _gamepad(cx: float, cy: float, color: str) -> str:
    return f"""
    <rect x="{cx-160}" y="{cy-75}" width="320" height="150" rx="75" fill="#fff" stroke="{color}" stroke-width="6"/>
    <circle cx="{cx-95}" cy="{cy-5}" r="30" fill="none" stroke="{color}" stroke-width="6"/>
    <line x1="{cx-125}" y1="{cy-5}" x2="{cx-65}" y2="{cy-5}" stroke="{color}" stroke-width="6"/>
    <line x1="{cx-95}" y1="{cy-35}" x2="{cx-95}" y2="{cy+25}" stroke="{color}" stroke-width="6"/>
    <circle cx="{cx+95}" cy="{cy-25}" r="13" fill="{color}"/>
    <circle cx="{cx+125}" cy="{cy+5}" r="13" fill="{color}" opacity="0.7"/>
    <circle cx="{cx+95}" cy="{cy+25}" r="13" fill="{color}" opacity="0.5"/>
    <circle cx="{cx+65}" cy="{cy+5}" r="13" fill="{color}" opacity="0.85"/>
    """


_GLYPH = {
    "mouse": _mouse,
    "keyboard": _keyboard,
    "headset": _headset,
    "webcam": _webcam,
    "dock": _dock,
    "gamepad": _gamepad,
}


def render_svg(sku: str, name: str, brand: str, category: str) -> str:
    color = ACCENT.get(category, "#2f6df6")
    glyph = _GLYPH.get(category, _mouse)(300, 300, color)
    name_lines = _wrap(name, 12)
    name_svg = "\n".join(
        f'<text x="300" y="{500 + i*34}" text-anchor="middle" font-family="Inter, system-ui, sans-serif" '
        f'font-size="30" font-weight="700" fill="#1f2937">{_esc(ln)}</text>'
        for i, ln in enumerate(name_lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600" width="600" height="600">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#eef2fb"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="600" height="600" rx="28" fill="url(#bg)"/>
  <rect x="18" y="18" width="564" height="564" rx="22" fill="none" stroke="#dbe3f3" stroke-width="2"/>
  <text x="40" y="62" font-family="Inter, system-ui, sans-serif" font-size="26" font-weight="800" fill="{color}" letter-spacing="1">{_esc(brand.upper())}</text>
  <text x="560" y="62" text-anchor="end" font-family="Inter, system-ui, sans-serif" font-size="20" font-weight="600" fill="#9aa6bd">{_esc(sku)}</text>
  {glyph}
  {name_svg}
  <rect x="220" y="560" width="160" height="6" rx="3" fill="{color}" opacity="0.6"/>
</svg>
"""


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    count = 0
    for p in PRODUCTS:
        sku = p["sku"]
        svg = render_svg(sku, p["name"], p.get("brand", "P4Gear"), p["category"])
        path = os.path.join(OUT_DIR, f"{sku}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        count += 1
        print(f"  wrote {sku}.svg ({p['category']})")
    print(f"done: {count} product images -> {OUT_DIR}")


if __name__ == "__main__":
    main()
