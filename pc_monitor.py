import io
import json
import re
import time
import urllib.request
import threading
import queue
import os
import sys
import ctypes
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

import serial
from PIL import Image, ImageDraw, ImageFont
from dashboard_pages import NetworkStats, draw_network, draw_clock

from minitoo_windows import (
    encode_image_payload,
    build_chunks,
    build_packet,
    CMD_APP_NEW_GIF_CMD2020,
)

from monitor_config import CONFIG

URL = CONFIG["sensor_url"]
PORT = CONFIG["port"]
INTERVAL = CONFIG["interval"]
JPEG_QUALITY = CONFIG["jpeg_quality"]

def read_sensors():
    with urllib.request.urlopen(URL, timeout=4) as response:
        root = json.load(response)

    sensors = {}

    def walk(node, path=()):
        if isinstance(node, list):
            for item in node:
                walk(item, path)
            return

        if not isinstance(node, dict):
            return

        name = node.get("Text", "")
        current = path + (name,)

        value = node.get("Value", "")
        if value and value != "Value":
            sensors[current] = value

        for child in node.get("Children", []):
            walk(child, current)

    walk(root)

    def get(device, category, sensor):
        for path, value in sensors.items():
            if (
                (not device or device in path)
                and len(path) >= 3
                and path[-2] == category
                and path[-1] == sensor
            ):
                match = re.search(r"-?\d+(?:[,.]\d+)?", str(value))
                if match:
                    return float(match.group().replace(",", "."))
        return None

    cpu = CONFIG["cpu_name"]
    gpu = CONFIG["gpu_name"]

    return {
        "cpu_temp": get(cpu, "Temperatures", CONFIG["cpu_temperature_sensor"]),
        "cpu_load": get(cpu, "Load", "CPU Total"),
        "gpu_temp": get(gpu, "Temperatures", "GPU Core"),
        "gpu_load": get(gpu, "Load", "GPU Core"),
        "ram": get("Total Memory", "Data", "Memory Used"),
        "vram": get(gpu, "Data", "GPU Memory Used"),
    }




PAGE_NAMES = ('Overview', 'Network', 'Clock')


def draw_screen(data, page=0):
    if page == 1:
        return draw_network(data.get('network'))
    if page == 2:
        return draw_clock()
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (160, 128), "#090D0A")
    d = ImageDraw.Draw(img)

    GREEN = "#65ED78"
    WHITE = "#FFFFFF"
    MUTED = "#90B89A"
    TRACK = "#25382B"

    try:
        label_font = ImageFont.truetype("arialbd.ttf", 12)
        temp_font = ImageFont.truetype("arialbd.ttf", 25)
        load_font = ImageFont.truetype("arialbd.ttf", 16)
        memory_font = ImageFont.truetype("arialbd.ttf", 12)
    except OSError:
        label_font = temp_font = load_font = memory_font = ImageFont.load_default()

    def gauge(cx, name, temp, load):
        bounds = (cx - 30, 17, cx + 30, 77)

        d.arc(bounds, 135, 405, fill=TRACK, width=6)

        if temp is not None:
            progress = max(0, min(1, (temp - 20) / 80))
            d.arc(bounds, 135, 135 + 270 * progress, fill=GREEN, width=6)

        d.text((cx, 5), name, font=label_font,
               fill=MUTED, anchor="mt")

        value = "--" if temp is None else str(round(temp))
        d.text((cx, 47), value, font=temp_font,
               fill=WHITE, anchor="mm")

        load_value = "--" if load is None else str(round(load))
        d.text((cx, 87), load_value + "%",
               font=load_font, fill=GREEN, anchor="mm")

    gauge(42, "CPU", data["cpu_temp"], data["cpu_load"])
    gauge(118, "GPU", data["gpu_temp"], data["gpu_load"])

    d.line((5, 102, 155, 102), fill=TRACK, width=1)

    ram = data["ram"]
    vram = data["vram"]

    ram_text = "--" if ram is None else f"{ram:.1f}"
    vram_text = "--" if vram is None else f"{vram / 1024:.1f}"

    d.text((5, 110), f"RAM {ram_text}G",
           font=memory_font, fill=WHITE)

    d.text((82, 110), f"VRAM {vram_text}G",
           font=memory_font, fill=WHITE)

    d.text((158, 101), f'{page + 1}/3', font=ImageFont.load_default(), fill=MUTED, anchor='rb')
    return img


class JoystickReader:
    """Only MiniToo USB HID; no global keyboard hooks or output reports."""

    def __init__(self):
        self.stop_event = threading.Event()
        self.changed = threading.Event()
        self.moves = queue.Queue()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.last_report = b''

    def on_input(self, report):
        previous = self.last_report
        self.last_report = report
        if report == previous:
            return
        delta = {b'\x00\x20\x00': -1, b'\x00\x10\x00': 1}.get(report)
        if delta:
            self.moves.put(delta)
            self.changed.set()

    def run(self):
        from usb_hid_probe import main as capture
        while not self.stop_event.is_set():
            try:
                self.last_report = b''
                capture(['--seconds', '86400', '--no-file'], self.on_input, self.stop_event)
            except Exception as exc:
                print('USB HID:', exc, flush=True)
            self.stop_event.wait(15)

    def start(self):
        self.thread.start()

    def page(self, current):
        self.changed.clear()
        while True:
            try:
                current = (current + self.moves.get_nowait()) % len(PAGE_NAMES)
            except queue.Empty:
                return current

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=2)


class BluetoothReader:
    """Single serial reader; preserves partial packets and logs all received bytes."""

    def __init__(self, dev):
        self.dev = dev
        self.packets = queue.Queue()
        self.stop_event = threading.Event()
        self.error = None
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path = Path(__file__).resolve().parent / ("minitoo_rx_" + stamp + ".log")
        self.log = self.path.open("w", encoding="utf-8", buffering=1)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        print("RX log:", self.path, flush=True)
        self.thread.start()

    def record(self, message):
        line = datetime.now().isoformat(timespec="milliseconds") + " " + message
        self.log.write(line + "\n")

    def run(self):
        buffer = bytearray()
        try:
            while not self.stop_event.is_set():
                data = self.dev.read(min(65536, max(1, self.dev.in_waiting)))
                if not data:
                    continue
                self.record("RAW " + data.hex(" "))
                buffer.extend(data)
                while buffer:
                    if buffer[0] != 1:
                        del buffer[0]
                        continue
                    if len(buffer) < 3:
                        break
                    length = int.from_bytes(buffer[1:3], "little")
                    if not 3 <= length <= 65535:
                        del buffer[0]
                        continue
                    size = length + 4
                    if len(buffer) < size:
                        break
                    packet = bytes(buffer[:size])
                    valid = (packet[-1] == 2 and
                             (sum(packet[1:-3]) & 0xFFFF) ==
                             int.from_bytes(packet[-3:-1], "little"))
                    if not valid:
                        self.record("INVALID " + packet.hex(" "))
                        del buffer[0]
                        continue
                    del buffer[:size]
                    self.packets.put(packet)
                    # Image-transfer acknowledgements remain in the log,
                    # but other packets are also highlighted on the console.
                    image_ack = (len(packet) >= 6 and
                                 packet[3:5] == b"\x04\x8b")
                    self.record("FRAME " + packet.hex(" "))
                    if not image_ack:
                        print("RX EVENT:", packet.hex(" "), flush=True)
        except Exception as exc:
            self.error = exc
            self.record("READER ERROR " + repr(exc))

    def close(self):
        self.stop_event.set()
        self.dev.cancel_read()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            # Never free OVERLAPPED buffers while the native read still uses them.
            os._exit(2)
        self.log.close()


def read_packet(reader, timeout=1.0):
    if reader.error is not None:
        raise RuntimeError("Bluetooth reader failed") from reader.error
    try:
        return reader.packets.get(timeout=timeout)
    except queue.Empty:
        return None


def transfer(dev, img, reader):
    payload = encode_image_payload(img, jpeg_quality=JPEG_QUALITY)
    chunks = build_chunks(payload)

    start_data = b"\x00" + len(payload).to_bytes(4, "little") + b"\x00"
    start = build_packet(CMD_APP_NEW_GIF_CMD2020, start_data)

    # Responses left over from a previous image must not drive this transfer.
    while True:
        try:
            reader.packets.get_nowait()
        except queue.Empty:
            break
    dev.write(start)

    sent = set()
    requests = 0
    deadline = time.monotonic() + 12
    last_request = time.monotonic()

    while time.monotonic() < deadline and requests < 100:
        response = read_packet(reader, timeout=0.4)
        if not response:
            if len(sent) == len(chunks) and time.monotonic() - last_request > 2.2:
                break
            continue

        if len(response) < 11:
            continue

        if response[3] != 0x04 or response[4] != 0x8B:
            continue

        # MiniToo sends 04 8B 55 00 01 after decoding/displaying the image.
        if response[5:8] == b'\x55\x00\x01' and len(sent) == len(chunks):
            return len(sent), len(chunks)

        if response[6] == 0x01:
            idx = int.from_bytes(response[7:9], "little")
            requests += 1
            last_request = time.monotonic()

            if 0 <= idx < len(chunks):
                dev.write(chunks[idx])
                sent.add(idx)

    return len(sent), len(chunks)


def write_status(**fields):
    path = Path(__file__).resolve().with_name('monitor_status.json')
    temporary = path.with_suffix('.tmp')
    status = dict(pid=os.getpid(), updated=datetime.now().isoformat(), port=PORT, **fields)
    temporary.write_text(json.dumps(status), encoding='utf-8')
    temporary.replace(path)


def refresh_delay(page, elapsed, now=None):
    if page == 2:
        now = now or datetime.now()
        return 60 - now.second - now.microsecond / 1_000_000
    return max(0.2, INTERVAL - elapsed)


def wait_for_refresh(joystick, page, elapsed, last_success):
    deadline = time.monotonic() + refresh_delay(page, elapsed)
    # Keep the supervisor heartbeat alive while the clock image is unchanged.
    while not joystick.changed.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if joystick.changed.wait(min(5, remaining)):
            break
        write_status(state='waiting', page=page, last_success=last_success)


def monitor(joystick):
    print("MiniToo PC Monitor")
    print("Press Ctrl+C to stop")

    page = 0
    failures = 0
    network = NetworkStats()
    last_success = None
    while True:
        try:
            with serial.Serial(PORT, timeout=0.1, write_timeout=3) as dev:
                print("Connected to", PORT)

                reader = BluetoothReader(dev)
                reader.start()
                try:
                    while True:
                        started = time.monotonic()

                        page = joystick.page(page)
                        try:
                            data = read_sensors() if page == 0 else {}
                        except Exception as exc:
                            print('Sensors unavailable:', exc)
                            data = dict.fromkeys(('cpu_temp', 'cpu_load', 'gpu_temp', 'gpu_load', 'ram', 'vram'))
                        try:
                            data['network'] = network.sample()
                        except Exception as exc:
                            print('Network unavailable:', exc)
                            data['network'] = None
                        # Consume new moves on the next iteration: this data belongs
                        # to the page selected before collection started.
                        image_minute = datetime.now().strftime('%Y%m%d%H%M')
                        img = draw_screen(data, page)
                        transfer_started = time.monotonic()
                        sent, total = transfer(dev, img, reader)
                        transfer_seconds = time.monotonic() - transfer_started

                        print(f"Transferred: {sent}/{total} chunks")
                        write_status(state='sent' if sent == total else 'incomplete',
                                     page=page, sent=sent, total=total, last_success=last_success)
                        if sent != total:
                            raise TimeoutError(f'Incomplete image: {sent}/{total}; reconnect {PORT}')
                        failures = 0
                        last_success = datetime.now().isoformat()
                        write_status(state='sent', page=page, sent=sent, total=total,
                                     last_success=last_success, transfer_seconds=round(transfer_seconds, 3))
                        print(f'Transfer time: {transfer_seconds:.3f}s page={page} quality={JPEG_QUALITY}')

                        elapsed = time.monotonic() - started
                        if page == 2 and image_minute != datetime.now().strftime('%Y%m%d%H%M'):
                            continue
                        wait_for_refresh(joystick, page, elapsed, last_success)
                finally:
                    reader.close()

        except KeyboardInterrupt:
            print("\nStopped")
            break
        except Exception as exc:
            failures += 1
            delay = min(60, 5 * 2 ** min(failures - 1, 4))
            print("Error:", exc)
            write_status(state='reconnecting', page=page, error=str(exc),
                         retry_seconds=delay, last_success=last_success)
            print(f'Retrying in {delay} seconds...')
            time.sleep(delay)


def main():
    # Held for the entire process lifetime, before either COM3 or HID is opened.
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, False, r'Local\MiniToo_PC_Monitor_COM3')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(mutex)
        return
    logger = logging.getLogger('minitoo')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(Path(__file__).resolve().with_name('monitor.log'),
                                  maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)

    class LogStream:
        def write(self, text):
            if text.strip():
                logger.info(text.rstrip())
        def flush(self):
            handler.flush()

    sys.stdout = sys.stderr = LogStream()
    print(f'Started pid={os.getpid()} script={Path(__file__).resolve()}')
    joystick = JoystickReader()
    joystick.start()
    try:
        monitor(joystick)
    finally:
        joystick.close()
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
