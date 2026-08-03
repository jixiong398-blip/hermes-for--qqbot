#!/usr/bin/env python3
"""AI & Tech Morning Briefing — 1920x1080, fixed layout."""

from PIL import Image, ImageDraw, ImageFont
import os

BG     = "#F0F2F5"
WHITE  = "#FFFFFF"
DARK   = "#1E272E"
MUTED  = "#636E72"
LINE_C = "#DFE6E9"

C = ["#6C5CE7","#FD79A8","#00CEC9","#FDCB6E","#E17055","#0984E3","#A29BFE"]

FB = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

W, H = 1920, 1080
M, GAP = 50, 20

def rr(d, xy, r, fill):
    x1,y1,x2,y2 = xy
    r = min(r, (y2-y1)//2, (x2-x1)//2)
    if r<=0: d.rectangle(xy, fill=fill); return
    d.pieslice([x1,y1,x1+2*r,y1+2*r],180,270,fill=fill)
    d.pieslice([x2-2*r,y1,x2,y1+2*r],270,360,fill=fill)
    d.pieslice([x1,y2-2*r,x1+2*r,y2],90,180,fill=fill)
    d.pieslice([x2-2*r,y2-2*r,x2,y2],0,90,fill=fill)
    d.rectangle([x1+r,y1,x2-r,y2],fill=fill)
    d.rectangle([x1,y1+r,x2,y2-r],fill=fill)

def wrap(t, f, mw, d):
    ls=[]; [ls.append(c) if not ls else (ls.append(c) if d.textbbox((0,0),ls[-1]+c,font=f)[2]>mw else (ls.__setitem__(-1,ls[-1]+c))) for c in t]; return ls

def draw_card(d, x, y, w, h, col, title, sub, body_lines, ft, fs, fb):
    rr(d,(x,y,x+w,y+h),12,WHITE)
    rr(d,(x+1,y+1,x+w-1,y+7),6,col)
    d.rectangle([x+7,y+1,x+w-7,y+7],fill=col)
    cx,cy=x+20,y+20
    d.text((cx,cy),title,fill=DARK,font=ft); cy+=ft.size+6
    d.text((cx,cy),sub,fill=col,font=fs); cy+=fs.size+10
    for l in body_lines: d.text((cx,cy),l,fill=MUTED,font=fb); cy+=fb.size+3

# ── Fonts ──
fT=ImageFont.truetype(FB,50); fD=ImageFont.truetype(FR,24)
fTag=ImageFont.truetype(FB,22); fHT=ImageFont.truetype(FB,34); fHS=ImageFont.truetype(FR,22)
fHB=ImageFont.truetype(FR,19)
fCT=ImageFont.truetype(FB,26); fCS=ImageFont.truetype(FR,18); fCB=ImageFont.truetype(FR,16)
fFT=ImageFont.truetype(FR,18)

img=Image.new("RGB",(W,H),BG)
d=ImageDraw.Draw(img)

# ── Header ──
d.text((M,30),"AI & Tech 早间简报",fill=DARK,font=fT)
d.text((M,90),"2026.06.08 · 周一",fill=MUTED,font=fD)
d.rectangle([M,125,280,130],fill=C[0])

USABLE = W - M*2  # 1820

# ── TOP ROW ──
# Hero left 490px, gap 20, then 3 cards side by side
hero_w = 490
side_w = (USABLE - hero_w - GAP - GAP*2) // 3  # (1820-490-20-40)/3 = 423
top_h = 410
Y1 = 155

# Hero
hx, hy = M, Y1
rr(d,(hx,hy,hx+hero_w,hy+top_h),12,WHITE)
rr(d,(hx+1,hy+1,hx+hero_w-1,hy+7),6,C[0])
d.rectangle([hx+7,hy+1,hx+hero_w-7,hy+7],fill=C[0])
d.text((hx+24,hy+24),"🔥 头条",fill=C[0],font=fTag)
d.text((hx+24,hy+56),"WWDC 2026 今日开幕",fill=DARK,font=fHT)
d.text((hx+24,hy+100),"Siri 1.2万亿参数 Gemini 全面重写",fill=C[0],font=fHS)
for i,t in enumerate(["Apple WWDC 2026 今天正式开幕！","Siri 由 Google Gemini 1.2万亿参数模型全面重写","iOS 27 · macOS 27 同步发布","AI 照片编辑 · 智能相机 · 写作工具升级"]):
    d.text((hx+24,hy+134+i*(fHB.size+5)),t,fill=MUTED,font=fHB)
rr(d,(hx+24,hy+top_h-50,hx+24+110,hy+top_h-46),4,C[0])
d.text((hx+24,hy+top_h-42),"WWDC 2026",fill=MUTED,font=fD)

# Side cards (3 horizontal)
sx = hx + hero_w + GAP
side_data = [
    ("SpaceX × Google","$300亿数据大单",["Google 向 SpaceX 支付 $90亿","AI 算力大单 · IPO在即"],C[1]),
    ("OpenAI Lockdown Mode","防提示注入攻击",["ChatGPT 锁定模式上线","禁用浏览/Agent/深度研究"],C[2]),
    ("Perplexity Search as Code","AI自写搜索管道",["不再调用固定API","AI自己写Python搜索代码"],C[3]),
]
for i,(t,sb,bl,c) in enumerate(side_data):
    cx = sx + i*(side_w+GAP)
    body = sum((wrap(l,fCB,side_w-40,d) for l in bl), [])
    draw_card(d,cx,hy,side_w,top_h,c,t,sb,body,fCT,fCS,fCB)

# ── BOTTOM ROW ──
bot_w = (USABLE - GAP*2) // 3  # (1820-40)/3 = 593
bot_h = 290
Y2 = Y1 + top_h + GAP  # 155+410+20=585

bot_data = [
    ("新模型 · 新突破","NVIDIA · Microsoft · Google",
     ["NVIDIA Nemotron 3 Ultra","  550B MoE · 55B激活 · Agent推理优化",
      "Microsoft MAI-Thinking-1","  中型推理 · 软件工程基准持平头部",
      "Google Agentic RAG","  多Agent协作 · 企业查询分解"],C[4]),
    ("📉 市场回调","AI芯片板块大跌",
     ["纳斯达克 -4.2% (4月以来最差单日)","标普500 -2.6%",
      "Broadcom 财报不及预期","  AI芯片狂热遭遇 Reality Check",
      "Nebius (NBIS) +300%","  AI基础设施赛道独秀"],C[5]),
    ("📰 更多资讯","今日值得关注",
     ["欧盟 AI Act 技术主权方案推进","  SpaceX IPO 前密集签约",
      "Honeywell 举办2026展望电话会","  CES 2026 机器人/AI预览",
      "Databricks Data+AI Summit","  Stanford AI 2026年度预测"],C[6]),
]

for i,(t,sb,bl,c) in enumerate(bot_data):
    bx = M + i*(bot_w+GAP)
    body = sum((wrap(l,fCB,bot_w-40,d) for l in bl), [])
    draw_card(d,bx,Y2,bot_w,bot_h,c,t,sb,body,fCT,fCS,fCB)

# ── Footer ──
fy = Y2 + bot_h + 22
d.line([(M,fy),(W-M,fy)],fill=LINE_C,width=1)
d.text((M,fy+10),"由 Hermes Agent · 長崎素世 自动生成 · 每日早8点 / 晚10点推送",fill=MUTED,font=fFT)

out = os.path.join(os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes")),
                    "output/infographic/latest/infographic-v4.png")
os.makedirs(os.path.dirname(out),exist_ok=True); img.save(out,"PNG")
print(f"Saved: {out} ({os.path.getsize(out)//1024} KB)")
