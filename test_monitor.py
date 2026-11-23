import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import pc_monitor
from monitor_config import load_config
from minitoo_windows import build_packet, build_chunks, encode_image_payload


class ProtocolTests(unittest.TestCase):
    def test_packet_checksum_length_and_payload_survive_framing(self):
        data = bytes(range(256))
        packet = build_packet(0x8B, data)
        self.assertEqual(packet[0], 1)
        self.assertEqual(packet[-1], 2)
        self.assertEqual(int.from_bytes(packet[1:3], 'little'), len(data) + 3)
        self.assertEqual(packet[4:-3], data)
        self.assertEqual(int.from_bytes(packet[-3:-1], 'little'), sum(packet[1:-3]) & 0xFFFF)

    def test_chunks_reassemble_payload_and_use_little_endian_indices(self):
        payload = bytes(range(256)) * 3 + b'last'
        chunks = build_chunks(payload)
        self.assertEqual(len(chunks), 4)
        for index, packet in enumerate(chunks):
            self.assertEqual(packet[3:5], b'\x8b\x01')
            self.assertEqual(int.from_bytes(packet[5:9], 'little'), len(payload))
            self.assertEqual(int.from_bytes(packet[9:11], 'little'), index)
        self.assertEqual(b''.join(p[11:-3] for p in chunks), payload)

    def test_jpeg_has_correct_header_and_declared_length(self):
        payload = encode_image_payload(Image.new('RGB', (80, 64), 'green'))
        self.assertEqual(payload[:7], b'\x23\x01\x00\x00\x08\x0a\x01')
        self.assertEqual(int.from_bytes(payload[7:11], 'big'), len(payload) - 11)
        self.assertEqual(payload[11:13], b'\xff\xd8')


class ConfigAndSensorTests(unittest.TestCase):
    def test_config_overrides_defaults_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            self.assertEqual(load_config(path)['port'], 'COM3')
            path.write_text(json.dumps({'port': 'COM8'}), encoding='utf-8')
            self.assertEqual(load_config(path)['port'], 'COM8')
            for invalid in ({'interval': 0}, {'jpeg_quality': 100}, {'port': ''}, {'typo': 1}):
                path.write_text(json.dumps(invalid), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_custom_cpu_name_temperature_and_decimal_comma(self):
        tree = {'Text': 'root', 'Children': [
            {'Text': 'Demo CPU', 'Children': [
                {'Text': 'Temperatures', 'Children': [{'Text': 'CPU Package', 'Value': '52,5 °C'}]},
                {'Text': 'Load', 'Children': [{'Text': 'CPU Total', 'Value': '25 %'}]},
            ]}]}
        with patch.dict(pc_monitor.CONFIG, cpu_name='Demo CPU', cpu_temperature_sensor='CPU Package'), \
                patch('pc_monitor.urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value.read.return_value = json.dumps(tree).encode()
            data = pc_monitor.read_sensors()
        self.assertEqual(data['cpu_temp'], 52.5)
        self.assertEqual(data['cpu_load'], 25)
        self.assertIsNone(data['gpu_temp'])

    def test_joystick_wraps_and_suppresses_held_reports(self):
        reader = pc_monitor.JoystickReader()
        reader.on_input(b'\x00\x20\x00')
        reader.on_input(b'\x00\x20\x00')
        self.assertEqual(reader.page(0), 2)
        reader.on_input(b'\x00\x00\x00')
        reader.on_input(b'\x00\x10\x00')
        self.assertEqual(reader.page(2), 0)

    def test_all_pages_render_without_sensors(self):
        data = dict.fromkeys(('cpu_temp', 'cpu_load', 'gpu_temp', 'gpu_load', 'ram', 'vram'))
        for page in range(3):
            self.assertEqual(pc_monitor.draw_screen(data, page).size, (160, 128))


if __name__ == '__main__':
    unittest.main()
