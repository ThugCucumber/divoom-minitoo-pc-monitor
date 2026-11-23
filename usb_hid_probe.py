"""Read-only MiniToo HID discovery/capture using built-in Windows APIs."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import time
import os
from pathlib import Path
from datetime import datetime

class GUID(c.Structure):
    _fields_ = [('a', w.DWORD), ('b', w.WORD), ('d', w.WORD), ('e', c.c_ubyte * 8)]

class OVERLAPPED(c.Structure):
    _fields_ = [('Internal', c.c_size_t), ('InternalHigh', c.c_size_t),
                ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('hEvent', w.HANDLE)]

def main(argv=None, on_input=None, stop_event=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=0)
    parser.add_argument('--no-file', action='store_true')
    args = parser.parse_args(argv)
    hid = c.WinDLL('hid', use_last_error=True)
    cfg = c.WinDLL('cfgmgr32', use_last_error=True)
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CreateEventW.argtypes = [c.c_void_p,w.BOOL,w.BOOL,w.LPCWSTR]
    kernel.CreateEventW.restype = w.HANDLE
    kernel.ReadFile.argtypes = [w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.POINTER(OVERLAPPED)]
    kernel.WaitForSingleObject.argtypes = [w.HANDLE,w.DWORD]
    kernel.GetOverlappedResult.argtypes = [w.HANDLE,c.POINTER(OVERLAPPED),c.POINTER(w.DWORD),w.BOOL]
    kernel.CancelIoEx.argtypes = [w.HANDLE,c.POINTER(OVERLAPPED)]
    hid.HidD_GetPreparsedData.argtypes = [w.HANDLE,c.POINTER(c.c_void_p)]
    hid.HidP_GetCaps.argtypes = [c.c_void_p,c.c_void_p]
    hid.HidP_GetButtonCaps.argtypes = [c.c_int,c.c_void_p,c.POINTER(c.c_ushort),c.c_void_p]
    hid.HidP_GetValueCaps.argtypes = [c.c_int,c.c_void_p,c.POINTER(c.c_ushort),c.c_void_p]
    hid.HidP_GetUsages.argtypes = [c.c_int,w.USHORT,w.USHORT,c.c_void_p,c.POINTER(w.ULONG),c.c_void_p,c.c_void_p,w.ULONG]
    hid.HidD_FreePreparsedData.argtypes = [c.c_void_p]
    guid = GUID()
    hid.HidD_GetHidGuid(c.byref(guid))
    size = w.ULONG()
    rc = cfg.CM_Get_Device_Interface_List_SizeW(c.byref(size),c.byref(guid),None,0)
    if rc: raise RuntimeError(('interface size',rc))
    buf = c.create_unicode_buffer(size.value)
    rc = cfg.CM_Get_Device_Interface_ListW(c.byref(guid),None,buf,size,0)
    if rc: raise RuntimeError(('interface list',rc))
    paths = [p for p in buf[:].split('\0') if 'vid_4c4a&pid_4e55' in p.lower()]
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    logpath = Path(__file__).with_name('usb_hid_' + stamp + '.jsonl')
    with open(os.devnull if args.no_file else logpath, 'w',encoding='utf-8',buffering=1) as log:
        def record(data):
            data = {'time': datetime.now().isoformat(timespec='milliseconds'), **data}
            line = json.dumps(data,ensure_ascii=False)
            print(line,flush=True)
            log.write(line+'\n')
        record({'event':'discovery','paths':paths})
        for path in paths:
            access = 0x80000000 if args.seconds else 0
            handle = kernel.CreateFileW(path,access,3,None,3,0x40000000,None)
            if handle == c.c_void_p(-1).value:
                record({'event':'open_error','error':c.get_last_error()})
                continue
            try:
                prep = c.c_void_p()
                if not hid.HidD_GetPreparsedData(handle,c.byref(prep)):
                    raise c.WinError(c.get_last_error())
                caps = (c.c_ushort * 32)()
                try:
                    status = hid.HidP_GetCaps(prep,c.byref(caps))
                    if status != 0x110000: raise RuntimeError(('HidP_GetCaps',status))
                    for kind, index, api in [('button',23,hid.HidP_GetButtonCaps),
                                             ('value',24,hid.HidP_GetValueCaps)]:
                        count_caps = c.c_ushort(caps[index])
                        if not count_caps.value: continue
                        raw_caps = c.create_string_buffer(72*count_caps.value)
                        result = api(0,raw_caps,c.byref(count_caps),prep)
                        if result != 0x110000: raise RuntimeError((kind,result))
                        for idx in range(count_caps.value):
                            raw = raw_caps.raw[idx*72:(idx+1)*72]
                            word = lambda offset: int.from_bytes(raw[offset:offset+2],'little')
                            record({'event':'input_cap','kind':kind,'usage_page':word(0),
                                    'report_id':raw[2],'is_range':bool(raw[12]),
                                    'usage_min_or_usage':word(56),'usage_max':word(58),
                                    'raw':raw.hex(' ')})
                finally:
                    hid.HidD_FreePreparsedData(prep)
                record({'event':'caps','usage':caps[0],'usage_page':caps[1],
                        'input_bytes':caps[2],'output_bytes':caps[3],'feature_bytes':caps[4]})
                if not args.seconds or not caps[2]: continue
                event = kernel.CreateEventW(None,True,False,None)
                if not event: raise c.WinError(c.get_last_error())
                try:
                    deadline = time.monotonic()+args.seconds
                    record({'event':'capture_start','seconds':args.seconds})
                    while time.monotonic()<deadline and not (stop_event and stop_event.is_set()):
                        ov = OVERLAPPED(hEvent=event)
                        data = c.create_string_buffer(caps[2])
                        count = w.DWORD()
                        kernel.ResetEvent.argtypes = [w.HANDLE]
                        kernel.ResetEvent(event)
                        ok = kernel.ReadFile(handle,data,len(data),c.byref(count),c.byref(ov))
                        if not ok and c.get_last_error()!=997:
                            record({'event':'read_error','error':c.get_last_error()})
                            break
                        if not ok:
                            wait = kernel.WaitForSingleObject(event,min(250,max(1,int((deadline-time.monotonic())*1000))))
                            if wait != 0:
                                kernel.CancelIoEx(handle,c.byref(ov))
                                kernel.GetOverlappedResult(handle,c.byref(ov),c.byref(count),True)
                                if wait == 258:
                                    continue
                                raise RuntimeError(('WaitForSingleObject',wait))
                            if not kernel.GetOverlappedResult(handle,c.byref(ov),c.byref(count),False):
                                record({'event':'result_error','error':c.get_last_error()})
                                break
                        decoded = []
                        report_prep = c.c_void_p()
                        if hid.HidD_GetPreparsedData(handle,c.byref(report_prep)):
                            try:
                                usages = (w.USHORT * 32)()
                                usage_count = w.ULONG(32)
                                result = hid.HidP_GetUsages(0,caps[1],0,usages,c.byref(usage_count),report_prep,data,count.value)
                                if result == 0x110000:
                                    decoded = [hex(u) for u in usages[:usage_count.value]]
                            finally:
                                hid.HidD_FreePreparsedData(report_prep)
                        record({'event':'input','hex':data.raw[:count.value].hex(' '),'usages':decoded})
                        if on_input:
                            on_input(data.raw[:count.value])
                    record({'event':'capture_end'})
                finally:
                    kernel.CloseHandle(event)
            finally:
                kernel.CloseHandle(handle)
    if not args.no_file:
        print('Log:',logpath,flush=True)

if __name__ == '__main__':
    main()
