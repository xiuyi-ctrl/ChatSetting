"""Generate the launcher foreground, themed layer, and preview."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'app/src/main/res/drawable-xxxhdpi'
OUT.mkdir(parents=True, exist_ok=True)
S = 4
FONT = ImageFont.truetype('C:/Windows/Fonts/STXINGKA.TTF', 25*S)

def draw_icon(monochrome=False):
    image = Image.new('RGBA', (108*S, 108*S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    q = lambda values: tuple(round(value*S) for value in values)
    back = '#FFFFFF' if monochrome else '#E8F7F2'
    front = '#FFFFFF' if monochrome else '#29B99B'
    draw.rounded_rectangle(q((25, 24, 69, 59)), radius=8*S, fill=back)
    draw.polygon([(29*S, 56*S), (27*S, 65*S), (39*S, 57*S)], fill=back)
    if not monochrome:
        draw.rectangle(q((33, 35, 57, 38)), fill='#526F73')
    draw.rounded_rectangle(q((45, 38, 83, 65)), radius=8*S, fill=front)
    draw.polygon([(70*S, 63*S), (79*S, 70*S), (78*S, 61*S)], fill=front)
    if not monochrome:
        draw.rectangle(q((53, 49, 74, 52)), fill='#103B3D')
    draw.text((54*S, 77*S), '咻咿', font=FONT, anchor='mm',
              fill='#FFFFFF' if monochrome else '#D9FFF2',
              stroke_width=0 if monochrome else S,
              stroke_fill='#0B2B32')
    return image

foreground = draw_icon()
foreground.save(OUT / 'ic_launcher_foreground.png')
draw_icon(monochrome=True).save(OUT / 'ic_launcher_monochrome.png')

preview = Image.new('RGBA', foreground.size, '#14243B')
preview.alpha_composite(foreground)
preview.resize((512, 512), Image.Resampling.LANCZOS).save(ROOT / 'icon-preview.png')
