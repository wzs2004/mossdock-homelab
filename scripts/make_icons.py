#!/usr/bin/env python3
"""为三个扩展生成 16/32/48/128 图标（无外部依赖，仅 Pillow）。"""
import pathlib
from PIL import Image, ImageDraw

def base(size=512):
    img = Image.new('RGBA', (size, size), (0,0,0,0))
    d = ImageDraw.Draw(img)
    r = int(size*0.22)
    # rounded square with vertical gradient
    grad = Image.new('RGB', (1, size))
    for y in range(size):
        t = y/size
        grad.putpixel((0,y), (int(14+10*t), int(35+70*t), int(55+80*t)))
    grad = grad.resize((size,size))
    mask = Image.new('L', (size,size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,size-1,size-1], radius=r, fill=255)
    img.paste(grad, (0,0), mask)
    return img, d

def icon_matching(size=512):
    img, d = base(size)
    w = int(size*0.055)
    # book
    d.rounded_rectangle([size*0.20,size*0.26,size*0.56,size*0.72], radius=size*0.04, outline=(236,250,255,255), width=w)
    d.line([size*0.38,size*0.26,size*0.38,size*0.72], fill=(236,250,255,255), width=w)
    # magnifier
    d.ellipse([size*0.50,size*0.48,size*0.78,size*0.76], outline=(125,231,209,255), width=int(w*1.15))
    d.line([size*0.74,size*0.72,size*0.86,size*0.84], fill=(125,231,209,255), width=int(w*1.5))
    return img

def icon_transcription(size=512):
    img, d = base(size)
    bars = [0.34,0.52,0.70,0.46,0.62,0.38,0.56]
    bw = size*0.055
    for i,h in enumerate(bars):
        x = size*0.16 + i*size*0.105
        cy = size*0.5
        hh = size*h*0.30
        d.rounded_rectangle([x,cy-hh,x+bw,cy+hh], radius=bw/2, fill=(125,231,209,255) if i%2 else (236,250,255,255))
    return img

def icon_lr(size=512):
    img, d = base(size)
    d.rounded_rectangle([size*0.14,size*0.22,size*0.62,size*0.60], radius=size*0.10, fill=(236,250,255,255))
    d.polygon([(size*0.28,size*0.60),(size*0.28,size*0.74),(size*0.46,size*0.60)], fill=(236,250,255,255))
    d.rounded_rectangle([size*0.40,size*0.42,size*0.86,size*0.78], radius=size*0.09, fill=(125,231,209,255))
    d.polygon([(size*0.74,size*0.78),(size*0.74,size*0.90),(size*0.58,size*0.78)], fill=(125,231,209,255))
    return img

ROOT = pathlib.Path(__file__).resolve().parent
LR = pathlib.Path(__file__).resolve().parent.parent / 'audiobookshelf-language-reactor-bridge' / 'extension'
TARGETS = {ROOT/'matching-extension': icon_matching, ROOT/'chrome-extension': icon_transcription, LR: icon_lr}
for folder, maker in TARGETS.items():
    out = folder/'icons'; out.mkdir(exist_ok=True)
    big = maker(512)
    for s in (16,32,48,128):
        big.resize((s,s), Image.LANCZOS).save(out/f'icon{s}.png', optimize=True)
    print('icons ->', out)
