"""Network counters and compact MiniToo network/clock layouts."""
from collections import deque
from datetime import datetime
import socket
import time

import psutil
from PIL import Image, ImageDraw, ImageFont

BG, WHITE, MUTED = '#090D0A', '#FFFFFF', '#90B89A'
GREEN, BLUE, GRID = '#65ED78', '#65BFFF', '#25382B'


def font(size):
    try:
        return ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', size)
    except OSError:
        return ImageFont.load_default(size=size)


class NetworkStats:
    def __init__(self):
        self.previous = None
        self.interface = None
        self.history = deque(maxlen=30)
        self.received = self.sent = 0

    def sample(self):
        now = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        states = psutil.net_if_stats()
        addresses = psutil.net_if_addrs()
        active = [name for name in counters if name in states and states[name].isup
                  and any(a.family == socket.AF_INET and not a.address.startswith('127.')
                          for a in addresses.get(name, []))]
        # Select one interface so VPN and physical traffic are not counted twice.
        physical = [name for name in active if states[name].speed > 0]
        candidates = physical or active
        name = self.interface if self.interface in candidates else max(
            candidates, key=lambda n: counters[n].bytes_recv + counters[n].bytes_sent,
            default=None)
        down = up = None
        if name:
            current = counters[name]
            if self.previous and name == self.interface:
                elapsed = max(.001, now - self.previous[0])
                rx = max(0, current.bytes_recv - self.previous[1])
                tx = max(0, current.bytes_sent - self.previous[2])
                down, up = rx / elapsed, tx / elapsed
                self.received += rx
                self.sent += tx
            else:
                self.history.clear()
            self.previous = (now, current.bytes_recv, current.bytes_sent)
        else:
            self.previous = None
            self.history.clear()
        self.interface = name
        self.history.append((down or 0, up or 0))
        ip = next((a.address for a in addresses.get(name, [])
                   if a.family == socket.AF_INET), '--')
        return dict(name=name, ip=ip, down=down, up=up, history=list(self.history),
                    received=self.received, sent=self.sent)


def rate(value):
    if value is None:
        return '--'
    mbits = value * 8 / 1_000_000
    return f'{mbits:.1f}' if mbits < 100 else f'{mbits:.0f}'


def draw_network(net):
    img = Image.new('RGB', (160, 128), BG)
    d = ImageDraw.Draw(img)
    net = net or {}
    d.text((6, 3), 'NETWORK', font=font(11), fill=MUTED)
    d.text((154, 3), 'Mbps', font=font(10), fill=MUTED, anchor='rt')
    d.text((6, 22), 'IN', font=font(10), fill=GREEN)
    d.text((85, 22), 'OUT', font=font(10), fill=BLUE)
    for x, key, color in [(6, 'down', GREEN), (85, 'up', BLUE)]:
        text = rate(net.get(key))
        size = 22
        while d.textlength(text, font=font(size)) > 70 and size > 12:
            size -= 1
        d.text((x, 34), text, font=font(size), fill=color)
    for y in (64, 79, 94):
        d.line((6, y, 153, y), fill=GRID)
    history = net.get('history', [])
    scale = max(125_000, max((max(p) for p in history), default=0))
    for index, color in [(0, GREEN), (1, BLUE)]:
        points = [(153 - (len(history)-1-i)*5, 94 - round(p[index]/scale*29))
                  for i, p in enumerate(history)]
        if len(points) > 1:
            d.line(points, fill=color, width=2)
    d.text((6, 97), net.get('ip', '--'), font=font(11), fill=WHITE)
    volume = (net.get('received', 0) + net.get('sent', 0)) / 1_000_000
    d.text((6, 113), f'SESSION {volume:.0f} MB' if net.get('name') else 'NO NETWORK',
           font=font(9), fill=MUTED)
    d.text((158, 126), '2/3', font=font(9), fill=MUTED, anchor='rb')
    return img


def draw_clock(now=None):
    now = now or datetime.now()
    img = Image.new('RGB', (160, 128), BG)
    d = ImageDraw.Draw(img)
    days = ('MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY')
    d.text((80, 9), days[now.weekday()], font=font(12), fill=MUTED, anchor='mt')
    d.text((80, 34), now.strftime('%H:%M'), font=font(46), fill=WHITE, anchor='mt')
    d.line((12, 91, 147, 91), fill=GRID, width=2)
    d.line((12, 91, 12 + round(now.minute / 59 * 135), 91), fill=GREEN, width=2)
    d.text((80, 103), now.strftime('%d.%m.%Y'), font=font(15), fill=GREEN, anchor='mt')
    d.text((158, 126), '3/3', font=font(9), fill=MUTED, anchor='rb')
    return img
