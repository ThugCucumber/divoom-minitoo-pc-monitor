"""Independent recovery for native crashes and stalled monitor workers."""
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

def main():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, False, r'Local\MiniToo_Monitor_Supervisor')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(mutex)
        return
    handler = RotatingFileHandler(ROOT / 'supervisor.log', maxBytes=500000,
                                  backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format='%(asctime)s %(message)s')
    status = ROOT / 'monitor_status.json'
    try:
        while True:
            # Error dialogs must not keep an already crashed worker alive.
            kernel.SetErrorMode(0x0001 | 0x0002)
            child = subprocess.Popen([sys.executable, str(ROOT / 'pc_monitor.py')],
                                     cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
            started = time.time()
            logging.info('Worker started pid=%s', child.pid)
            while child.poll() is None:
                time.sleep(5)
                try:
                    heartbeat = status.stat().st_mtime
                except OSError:
                    heartbeat = 0
                if time.time() - max(started, heartbeat) > 120:
                    logging.error('Worker stalled pid=%s; terminating', child.pid)
                    child.kill()
                    break
            child.wait()
            logging.warning('Worker exited code=%s; restart in 10 seconds', child.returncode)
            time.sleep(10)
    finally:
        kernel.CloseHandle(mutex)

if __name__ == '__main__':
    main()
