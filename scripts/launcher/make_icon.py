from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


output = Path(__file__).with_name("yue2.ico")
canvas = Image.new("RGBA", (256, 256), (245, 247, 252, 255))
draw = ImageDraw.Draw(canvas)
draw.rounded_rectangle((12, 12, 244, 244), radius=58, fill=(249, 239, 245, 255), outline=(241, 95, 140, 255), width=10)
draw.ellipse((52, 52, 204, 204), fill=(50, 64, 96, 255))

try:
    font = ImageFont.truetype("arialbd.ttf", 90)
except OSError:
    font = ImageFont.load_default()

text = "Y2"
box = draw.textbbox((0, 0), text, font=font)
x = (256 - (box[2] - box[0])) / 2
y = (256 - (box[3] - box[1])) / 2 - 5
draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
canvas.save(output, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(output)
