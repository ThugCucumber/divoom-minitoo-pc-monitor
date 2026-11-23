"""Local JSON settings. Importing this module never opens hardware."""
import json
from pathlib import Path

DEFAULTS = {
    'port': 'COM3',
    'sensor_url': 'http://localhost:8085/data.json',
    'interval': 3,
    'jpeg_quality': 75,
    'cpu_name': '',
    'gpu_name': '',
    'cpu_temperature_sensor': 'Core (Tctl/Tdie)',
}


def load_config(path=None):
    path = Path(path) if path else Path(__file__).with_name('config.json')
    config = dict(DEFAULTS)
    if path.exists():
        overrides = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(overrides, dict):
            raise ValueError('config.json must contain a JSON object')
        unknown = overrides.keys() - DEFAULTS.keys()
        if unknown:
            raise ValueError(f'Unknown settings: {sorted(unknown)}')
        config.update(overrides)
    for key in ('port', 'sensor_url', 'cpu_name', 'gpu_name', 'cpu_temperature_sensor'):
        if not isinstance(config[key], str):
            raise ValueError(f'{key} must be a string')
    if not config['port'].strip():
        raise ValueError('port must not be empty')
    interval = config['interval']
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not 0.2 <= interval <= 60:
        raise ValueError('interval must be between 0.2 and 60 seconds')
    quality = config['jpeg_quality']
    if isinstance(quality, bool) or not isinstance(quality, int) or not 1 <= quality <= 95:
        raise ValueError('jpeg_quality must be an integer between 1 and 95')
    return config


CONFIG = load_config()
