#!/usr/bin/env python3
"""Generate a beautiful evening news image."""

from PIL import Image, ImageDraw, ImageFont
import os

# Font paths
FONT_SANS_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_SANS_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FONT_SERIF_BOLD = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"

OUTPUT_PATH = os.path.join(os.getenv("HERMES_HOME", os.path.join(os.path.expanduser("~"), ".hermes")), "cache", "images", "evening_news_0608.png")

# News content
news_data = [
    ("🌍 中东局势", "伊朗对以色列北部发动\"警告性\"导弹打击，回应以方\n扩大在黎巴嫩的军事行动。特朗普劝以方克制，中\n东局势再度升级，原油价格应声上涨。"),
    ("📉 金融市场", "沪指失守4000点，A股三大股指集体下挫。韩国股市\n暴跌触发熔断。央行连续第19个月增持黄金。"),
    ("🍎 Apple WWDC", "库克最后一次主持WWDC。全新Siri搭载Google\nGemini模型（1.2万亿参数）。iOS 27支持用户自选\nAI模型——ChatGPT、Gemini或Claude。"),
    ("🔧 国产芯片", "华为昇腾910C芯片成功完成1.6万亿参数DeepSeek\n全参数后训练！国产算力历史性突破。"),
    ("🧠 脑机接口", "全国首例！湘雅医院脑机接口智能视网膜临床试验\n成功，首例受试者已能分辨字符并独立导航。"),
    ("🚀 AI产业", "SpaceX（含xAI）6月11日定价IPO，目标估值\n1.75万亿美元。AI聊天机器人份额：ChatGPT 54.7%\nGemini 27.4%  Claude增长306%至8.2%"),
]

SECTION_COLORS = [
    "#E85850",  # red for war
    "#F0A030",  # orange for finance
    "#4A9BE8",  # blue for tech
    "#E85850",  # red for chip
    "#50C878",  # green for bci
    "#9B59B6",  # purple for ai
]

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def create_news_image():
    # Image dimensions
    W, H = 1080, 1920
    
    # Create image
    img = Image.new("RGB", (W, H), "#0B0E1A")
    draw = ImageDraw.Draw(img)
    
    # Load fonts
    title_font = ImageFont.truetype(FONT_SERIF_BOLD, 58)
    date_font = ImageFont.truetype(FONT_SANS_BOLD, 26)
    section_font = ImageFont.truetype(FONT_SANS_BOLD, 30)
    body_font = ImageFont.truetype(FONT_SANS_REG, 24)
    footer_font = ImageFont.truetype(FONT_SANS_REG, 20)
    
    # ---- Top decorative bar ----
    bar_grad_top = "#1A1F3A"
    bar_grad_bot = "#0B0E1A"
    for y in range(0, 120):
        r = int(0x1A + (0x0B - 0x1A) * y / 120)
        g = int(0x1F + (0x0E - 0x1F) * y / 120)
        b = int(0x3A + (0x1A - 0x3A) * y / 120)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    
    # Accent line
    draw.rectangle([(60, 110), (1020, 114)], fill="#E85850")
    
    # Title
    draw.text((60, 30), "晚间新闻速递", fill="#FFFFFF", font=title_font)
    
    # Date
    draw.text((60, 80), "2026年6月8日 · 星期一", fill="#8899BB", font=date_font)
    
    # ---- Sections ----
    y_start = 150
    section_height = 245
    gap = 16
    
    for i, (title, body) in enumerate(news_data):
        y = y_start + i * (section_height + gap)
        
        # Background card
        card_color = (18, 22, 45) if i % 2 == 0 else (22, 26, 50)
        draw.rounded_rectangle([(40, y), (1040, y + section_height)], 
                               radius=12, fill=card_color)
        
        # Left accent bar
        color = hex_to_rgb(SECTION_COLORS[i])
        draw.rounded_rectangle([(40, y), (48, y + section_height)], 
                               radius=4, fill=color)
        
        # Section title
        draw.text((68, y + 16), title, fill=SECTION_COLORS[i], font=section_font)
        
        # Separator line
        draw.line([(68, y + 52), (1000, y + 52)], fill=(40, 45, 75), width=1)
        
        # Body text
        draw.text((68, y + 64), body, fill="#C8CCD8", font=body_font, spacing=8)
    
    # ---- Footer ----
    footer_y = y_start + 6 * (section_height + gap) + 30
    draw.line([(60, footer_y), (1020, footer_y)], fill=(40, 45, 75), width=1)
    draw.text((540, footer_y + 14), "由素世为您播报 · 晚安", 
              fill="#667799", font=footer_font, anchor="mt")
    
    # ---- Bottom gradient ----
    for y in range(H - 60, H):
        t = (y - (H - 60)) / 60
        r = int(0x0B + (0x1A - 0x0B) * t)
        g = int(0x0E + (0x1F - 0x0E) * t)
        b = int(0x1A + (0x3A - 0x1A) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    
    # ---- Corner decoration ----
    # Top-right small lines
    for j in range(3):
        x = 1020 + j * 12
        draw.line([(x, 20), (x + 6, 20)], fill="#E85850", width=2)
    
    img.save(OUTPUT_PATH, quality=95)
    print(f"Image saved to {OUTPUT_PATH}")
    return OUTPUT_PATH

if __name__ == "__main__":
    create_news_image()
