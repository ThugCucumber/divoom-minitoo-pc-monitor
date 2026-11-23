"""Render documentation images using synthetic data; no hardware access."""
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from dashboard_pages import draw_clock, draw_network
from pc_monitor import draw_screen


def main():
    target = Path(__file__).parent / 'docs' / 'images'
    target.mkdir(parents=True, exist_ok=True)
    frames = [
        draw_screen(dict(cpu_temp=54, cpu_load=32, gpu_temp=61, gpu_load=78, ram=12.4, vram=6144)),
        draw_network(dict(name='Demo Ethernet', ip='192.0.2.10', down=8_100_000,
                          up=1_450_000, received=920_000_000, sent=180_000_000,
                          history=[(2e6 + (i * 7919 % 27) * 240000,
                                    3e5 + (i * 7919 % 13) * 100000) for i in range(30)])),
        draw_clock(datetime(2026, 11, 23, 18, 42)),
    ]
    canvas = Image.new('RGB', (1120, 400), '#101914')
    draw = ImageDraw.Draw(canvas)
    label_font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 18)
    for index, (frame, name) in enumerate(zip(frames, ('OVERVIEW', 'NETWORK', 'CLOCK'))):
        frame.save(target / (name.lower() + '.png'))
        x = 40 + index * 360
        draw.rounded_rectangle((x-8, 62, x+328, 334), radius=14, fill='#25382B')
        canvas.paste(frame.resize((320, 256), Image.Resampling.NEAREST), (x, 70))
        draw.text((x, 355), f'0{index+1}  {name}', fill='#90B89A', font=label_font)
    draw.text((40, 20), 'DIVOOM MINITOO / PC MONITOR', fill='#65ED78', font=label_font)
    canvas.save(target / 'screens.png')
    print('Rendered synthetic previews:', target)


if __name__ == '__main__':
    main()
