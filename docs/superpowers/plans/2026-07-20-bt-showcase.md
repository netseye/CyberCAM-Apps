# BT Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build 「蓝牙展示」 — the repo's first Bluetooth app — a 3-mode showcase (adapter info+control, scan, l2ping range test) running on `bluetoothctl` (D-Bus, non-root) + `sudo l2ping`, with no camera.

**Architecture:** Reuse the fun-cam/imu-attitude shell (Display 640×480 + freetype Chinese text + TouchInput + Key + direction flip) but **without** the camera. Pure parsing logic lives in `core.py` (Mac-testable with captured sample strings); subprocess wrappers in `btctl.py` (device-only); UI + background threads in `main.py`. All blocking IO runs in background threads so the display loop never stalls.

**Tech Stack:** Python 3, OpenCV/freetype, WalnutPi Display, `bluetoothctl` (BlueZ D-Bus CLI), `l2ping` (sudo). Pure-stdlib parsing (regex). Plain `assert` unit tests runnable on Mac (no pytest/cv2 needed).

**Verified device facts (do not re-derive):**
- `bluetoothctl show` is one-shot when stdout isn't a TTY; returns full adapter info (see sample below).
- `bluetoothctl devices` is one-shot; returns `Device <MAC> <Name>` lines (Name may be Chinese, alnum, or MAC-with-dashes fallback).
- `bluetoothctl --timeout N scan on` scans ~N s then exits; devices are cached and read back via `bluetoothctl devices`.
- `bluetoothctl discoverable on|off` and `pairable on|off` are one-shot and work **non-root**.
- **`paired-devices` is invalid** in this bluetoothctl version → do NOT use it; use `devices` only.
- `sudo l2ping -c 1 -s 10 <MAC>` hangs (no output) on non-responsive peers and prints `time X ms` on success; wrap in `timeout 3`. pi has **passwordless sudo**.
- No dbus-python/pybluez/bleak; pyserial present but unused.

**Captured real output samples (use verbatim in tests):**

`bluetoothctl show` excerpt:
```
Controller 10:11:12:13:14:15 (public)
	Manufacturer: 0x0b3b (2875)
	Name: WalnutPi
	Alias: WalnutPi
	Class: 0x00400000 (4194304)
	Powered: yes
	Discoverable: no
	Pairable: no
	UUID: Generic Access Profile    (00001800-0000-1000-8000-00805f9b34fb)
	UUID: A/V Remote Control        (0000110e-0000-1000-8000-00805f9b34fb)
	Discovering: no
```

`bluetoothctl devices` excerpt:
```
Device C5:F3:C9:F4:3E:DF C5-F3-C9-F4-3E-DF
Device F8:D5:54:63:47:DB midea
Device C0:09:25:7F:D4:01 前台50寸右侧
Device C7:7F:5B:39:4E:F8 JD-669S_f84e39
```

`l2ping` success (representative; parser matches `time X ms`):
```
Ping F8:D5:54:63:47:DB (midea) - 10 bytes of data
10 bytes from F8:D5:54:63:47:DB id 0 time 12.34 ms
1 sent, 1 received, 0% loss
```
`l2ping` failure = empty string (timeout, no response).

---

## Task 1: Scaffold app structure

**Files:**
- Create: `app/bt-showcase/app.txt`
- Create: `app/bt-showcase/run.sh`

- [ ] **Step 1: Create app.txt**

`app/bt-showcase/app.txt`:
```
name_cn="蓝牙展示"
name_en="BT Showcase"
index=22
```

- [ ] **Step 2: Create run.sh**

`app/bt-showcase/run.sh`:
```bash
python main.py
```

- [ ] **Step 3: Commit**

```bash
cd /Users/netseye/Documents/cybercam/CyberCAM-Apps
git add app/bt-showcase/app.txt app/bt-showcase/run.sh
git commit -m "feat(bt-showcase): scaffold app structure"
```

---

## Task 2: core.py — parse_show (TDD)

**Files:**
- Create: `app/bt-showcase/core.py`
- Create: `app/bt-showcase/tests/test_parse_show.py`

- [ ] **Step 1: Write the failing test**

`app/bt-showcase/tests/test_parse_show.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import parse_show

SAMPLE = """Controller 10:11:12:13:14:15 (public)
\tManufacturer: 0x0b3b (2875)
\tName: WalnutPi
\tAlias: WalnutPi
\tClass: 0x00400000 (4194304)
\tPowered: yes
\tDiscoverable: no
\tPairable: no
\tUUID: Generic Access Profile    (00001800-0000-1000-8000-00805f9b34fb)
\tUUID: A/V Remote Control        (0000110e-0000-1000-8000-00805f9b34fb)
\tDiscovering: no
"""

g = parse_show(SAMPLE)
assert g['mac'] == "10:11:12:13:14:15", g['mac']
assert g['name'] == "WalnutPi"
assert g['alias'] == "WalnutPi"
assert g['powered'] is True
assert g['discoverable'] is False
assert g['pairable'] is False
assert "00001800" in g['uuids'][0], g['uuids']
assert len(g['uuids']) == 2

# empty / unavailable input -> safe defaults, available False
g2 = parse_show("")
assert g2['available'] is False
assert g2['mac'] == ""
assert g2['uuids'] == []
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 app/bt-showcase/tests/test_parse_show.py`
Expected: `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Write minimal implementation**

`app/bt-showcase/core.py`:
```python
'''蓝牙展示 (BT Showcase) 纯解析层。不依赖 cv2 / walnutpi / subprocess,可在 Mac 上单测。'''
import re


def _line_bool(text, key):
    '''从 "Key: yes/no" 取布尔,缺省 False。'''
    m = re.search(r'^\t?' + re.escape(key) + r':\s*(yes|no)', text, re.M)
    return bool(m and m.group(1) == 'yes')


def parse_show(text):
    '''解析 `bluetoothctl show` 全文 -> dict。空文本返回 available=False 的安全默认。'''
    out = {'available': False, 'mac': '', 'name': '', 'alias': '',
           'class': '', 'powered': False, 'discoverable': False,
           'pairable': False, 'discovering': False, 'uuids': []}
    if not text or 'Controller' not in text:
        return out
    out['available'] = True
    m = re.search(r'Controller\s+([0-9A-Fa-f:]{17})', text)
    if m:
        out['mac'] = m.group(1)
    for key in ('name', 'alias', 'class'):
        mm = re.search(r'^\t?' + key + r':\s*(.+)$', text, re.M)
        if mm:
            out[key] = mm.group(1).strip()
    out['powered'] = _line_bool(text, 'Powered')
    out['discoverable'] = _line_bool(text, 'Discoverable')
    out['pairable'] = _line_bool(text, 'Pairable')
    out['discovering'] = _line_bool(text, 'Discovering')
    for mm in re.finditer(r'UUID:\s+.*\(([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\)', text):
        out['uuids'].append(mm.group(1))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 app/bt-showcase/tests/test_parse_show.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/bt-showcase/core.py app/bt-showcase/tests/test_parse_show.py
git commit -m "feat(bt-showcase): core parse_show with unit tests"
```

---

## Task 3: core.py — parse_devices + paginate (TDD)

**Files:**
- Modify: `app/bt-showcase/core.py` (append functions)
- Create: `app/bt-showcase/tests/test_parse_devices.py`

- [ ] **Step 1: Write the failing test**

`app/bt-showcase/tests/test_parse_devices.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import parse_devices, paginate

SAMPLE = """Device C5:F3:C9:F4:3E:DF C5-F3-C9-F4-3E-DF
Device F8:D5:54:63:47:DB midea
Device C0:09:25:7F:D4:01 前台50寸右侧
Device C7:7F:5B:39:4E:F8 JD-669S_f84e39
"""

devs = parse_devices(SAMPLE)
assert len(devs) == 4, devs
assert devs[0] == {'mac': 'C5:F3:C9:F4:3E:DF', 'name': 'C5-F3-C9-F4-3E-DF'}, devs[0]
assert devs[1] == {'mac': 'F8:D5:54:63:47:DB', 'name': 'midea'}, devs[1]
assert devs[2]['name'] == '前台50寸右侧', devs[2]
assert devs[3]['name'] == 'JD-669S_f84e39'
# trailing spaces in name stripped
assert parse_devices("Device AA:BB:CC:DD:EE:FF  hello  \n")[0]['name'] == 'hello'
# empty -> []
assert parse_devices("") == []
assert parse_devices("no device lines here") == []

# paginate
items = list(range(7))
page, total = paginate(items, page=0, per_page=3)
assert page == [0, 1, 2] and total == 3
page, total = paginate(items, page=2, per_page=3)
assert page == [6] and total == 3
page, total = paginate(items, page=5, per_page=3)  # out of range clamps
assert page == [6] and total == 3
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 app/bt-showcase/tests/test_parse_devices.py`
Expected: `ImportError: cannot import name 'parse_devices'`

- [ ] **Step 3: Append implementation to core.py**

Append to `app/bt-showcase/core.py`:
```python
def parse_devices(text):
    '''解析 `bluetoothctl devices` -> [{'mac','name'}, ...]。'''
    out = []
    for m in re.finditer(r'^Device\s+([0-9A-Fa-f:]{17})\s+(.*)$', text, re.M):
        out.append({'mac': m.group(1), 'name': m.group(2).strip()})
    return out


def paginate(items, page, per_page):
    '''分页纯函数。返回 (当前页列表, 总页数)。page 越界自动夹到最后一页。'''
    n = len(items)
    total = max(1, (n + per_page - 1) // per_page)
    page = max(0, min(page, total - 1))
    start = page * per_page
    return items[start:start + per_page], total
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 app/bt-showcase/tests/test_parse_devices.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/bt-showcase/core.py app/bt-showcase/tests/test_parse_devices.py
git commit -m "feat(bt-showcase): core parse_devices + paginate with tests"
```

---

## Task 4: core.py — parse_l2ping (TDD)

**Files:**
- Modify: `app/bt-showcase/core.py` (append function)
- Create: `app/bt-showcase/tests/test_parse_l2ping.py`

- [ ] **Step 1: Write the failing test**

`app/bt-showcase/tests/test_parse_l2ping.py`:
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import parse_l2ping

# success (representative BlueZ wording; parser matches "time X ms")
ok = parse_l2ping("Ping F8:D5:54:63:47:DB (midea) - 10 bytes of data\n"
                  "10 bytes from F8:D5:54:63:47:DB id 0 time 12.34 ms\n"
                  "1 sent, 1 received, 0% loss\n")
assert ok == {'ok': True, 'ms': 12.34}, ok

# alternate success wording still parses
ok2 = parse_l2ping("response .. time 8 ms")
assert ok2 == {'ok': True, 'ms': 8.0}, ok2

# failure: empty (timeout, no response)
assert parse_l2ping("") == {'ok': False, 'ms': None}
# failure: text without "time X ms"
assert parse_l2ping("Connect: Connection refused\n") == {'ok': False, 'ms': None}
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 app/bt-showcase/tests/test_parse_l2ping.py`
Expected: `ImportError: cannot import name 'parse_l2ping'`

- [ ] **Step 3: Append implementation to core.py**

Append to `app/bt-showcase/core.py`:
```python
def parse_l2ping(text):
    '''解析 `sudo l2ping -c 1 -s 10 <mac>` 输出。
    成功(含 "time X ms")-> {'ok':True,'ms':float}; 否则(空/无响应)-> {'ok':False,'ms':None}。'''
    m = re.search(r'time\s+([\d.]+)\s*ms', text or '')
    if m:
        return {'ok': True, 'ms': float(m.group(1))}
    return {'ok': False, 'ms': None}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 app/bt-showcase/tests/test_parse_l2ping.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/bt-showcase/core.py app/bt-showcase/tests/test_parse_l2ping.py
git commit -m "feat(bt-showcase): core parse_l2ping with tests"
```

---

## Task 5: btctl.py — subprocess wrappers (device-verified)

**Files:**
- Create: `app/bt-showcase/btctl.py`

**Note:** These call `bluetoothctl`/`l2ping` and only run on-device. Verify by deploying + a one-off capture script.

- [ ] **Step 1: Write btctl.py**

`app/bt-showcase/btctl.py`:
```python
'''蓝牙展示 subprocess 封装(设备专用)。走 bluetoothctl(D-Bus,非root)+ sudo l2ping。'''
import subprocess
import core


def _run(cmd, timeout):
    '''跑命令,返回 stdout 文本(超时/失败返回 '')。'''
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.stdout or ''
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ''


def adapter_info(timeout=3):
    '''`bluetoothctl show` -> core.parse_show 的 dict。'''
    return core.parse_show(_run('bluetoothctl show', timeout))


def scan_devices(seconds=8):
    '''扫描 seconds 秒后返回 parse_devices 的列表(后台/前台均可,本身阻塞 seconds 秒)。'''
    _run('bluetoothctl --timeout %d scan on' % int(seconds), seconds + 2)
    return core.parse_devices(_run('bluetoothctl devices', 3))


def set_discoverable(on, timeout=3):
    '''切换可发现性(非root)。'''
    val = 'on' if on else 'off'
    _run('bluetoothctl discoverable %s' % val, timeout)


def l2ping_once(mac, timeout=3):
    '''sudo l2ping 一次 -> core.parse_l2ping 的 dict。无响应时阻塞到 timeout,返回 ok=False。'''
    out = _run('sudo l2ping -c 1 -s 10 %s' % mac, timeout)
    return core.parse_l2ping(out)
```

- [ ] **Step 2: Deploy + verify on device**

```bash
cd /Users/netseye/Documents/cybercam/CyberCAM-Apps
expect /tmp/scp.exp app/bt-showcase/core.py pi@10.10.11.213:/data/app/bt-showcase/core.py
expect /tmp/scp.exp app/bt-showcase/btctl.py pi@10.10.11.213:/data/app/bt-showcase/btctl.py
expect /tmp/sshcmd.exp "cd /data/app/bt-showcase && python3 -c 'import btctl; print(btctl.adapter_info()[\"mac\"]); print(len(btctl.scan_devices(4))); print(btctl.l2ping_once(\"F8:D5:54:63:47:DB\"))'"
```
Expected: prints the MAC `10:11:12:13:14:15`, a device count `>= 0`, and a dict like `{'ok': False, 'ms': None}` (or `{'ok': True, 'ms': ...}` if a responsive peer exists).

- [ ] **Step 3: Commit**

```bash
git add app/bt-showcase/btctl.py
git commit -m "feat(bt-showcase): btctl subprocess wrappers (adapter/scan/l2ping)"
```

---

## Task 6: main.py shell skeleton (Display/Touch/Key/text/mode router, no camera)

**Files:**
- Create: `app/bt-showcase/main.py`

This reuses the **proven** fun-cam shell code (text, `_eviocgabs`, `TouchInput`, `Key`) verbatim — minus the camera and light-paint globals.

- [ ] **Step 1: Write main.py shell**

`app/bt-showcase/main.py`:
```python
'''
蓝牙展示 (BT Showcase) —— 多模式蓝牙能力展示(无摄像头)。

模式:
  1) 适配器   —— 本机蓝牙信息 + 可发现开关
  2) 扫描     —— 扫描周边设备,点选一个作为测距目标
  3) 测距     —— 对选中设备反复 l2ping,显示延迟/可达柱状图

交互:
  - 触摸左/右:上一/下一模式;触摸中间:模式内动作(开关/扫描/清空)
  - 板载按键 KEY:下一模式
'''

import cv2
import fcntl
import os
import struct
import threading
import time
from collections import deque

import numpy as np
from walnutpi import Display, direction

import core

# ----------------------------- 配置 ---------------------------------
W, H = 640, 480
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
FONT_H = 24
MODES = ["适配器", "扫描", "测距"]


# ----------------------------- 文字 ---------------------------------
ft = cv2.freetype.createFreeType2()
ft.loadFontData(FONT, 0)


def text(img, s, org, color=(0, 255, 0), h=FONT_H):
    '''org 为文字左上角。'''
    ft.putText(img=img, text=str(s), org=org, fontHeight=h,
               color=color, thickness=-1, line_type=cv2.LINE_AA,
               bottomLeftOrigin=False)
    return img


# ----------------------------- 触摸输入 -----------------------------
EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14a


def _eviocgabs(fd, axis):
    '''读 ABS 轴的 min/max(EVIOCGABS)。dir=READ=2 (EVIOCGABS 是 _IOR;3=_IOWR 会被内核拒为 EINVAL -> 退化到 0,1)。'''
    op = (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + axis)
    buf = bytearray(24)
    try:
        fcntl.ioctl(fd, op, buf, True)
        _, lo, hi, _, _, _ = struct.unpack('<6i', bytes(buf))
        if hi > lo:
            return lo, hi
    except OSError:
        pass
    return 0, 1


class TouchInput:
    '''后台线程读 /dev/input/event0。落笔按区域入队;另支持长按检测。'''

    def __init__(self, device="/dev/input/event0", flipped=False, long_thr=1.2):
        self.device = device
        self.flipped = flipped
        self.long_thr = long_thr
        self.taps = deque()
        self.running = False
        self._x = self._y = 0
        self._minx = self._maxx = None
        self._down = False
        self._down_t = 0.0
        self._long_fired = False
        try:
            fd = os.open(device, os.O_RDONLY)
            self._minx, self._maxx = _eviocgabs(fd, ABS_X)
            os.close(fd)
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self.running = True
            self._thread.start()
            print(f"[touch] {device} 范围 x:[{self._minx},{self._maxx}]")
        except OSError as e:
            self._thread = None
            print(f"[touch] 不可用({e}),仅用按键")

    def _loop(self):
        try:
            fd = os.open(self.device, os.O_RDONLY)
        except OSError:
            return
        while self.running:
            try:
                data = os.read(fd, 24)
            except OSError:
                break
            if len(data) < 24:
                continue
            _, _, typ, code, val = struct.unpack('<QQHHi', data)
            if typ == EV_ABS and code == ABS_X:
                self._x = val
            elif typ == EV_ABS and code == ABS_Y:
                self._y = val
            elif typ == EV_KEY and code == BTN_TOUCH:
                if val == 1:
                    self._down = True
                    self._down_t = time.monotonic()
                    self._long_fired = False
                    self.taps.append((self._x, self._y))
                else:
                    self._down = False

    def poll_region(self):
        '''取一次落笔,返回 "LEFT"/"CENTER"/"RIGHT" 或 None。'''
        if not self.taps or self._maxx is None:
            return None
        x, _ = self.taps.popleft()
        nx = (x - self._minx) / (self._maxx - self._minx)
        nx = max(0.0, min(1.0, nx))
        if self.flipped:
            nx = 1.0 - nx
        if nx < 0.33:
            return "LEFT"
        if nx > 0.66:
            return "RIGHT"
        return "CENTER"


# ----------------------------- 按键 ---------------------------------
class Key:
    def __init__(self):
        self._beep = self._led = None
        try:
            import board
            from digitalio import DigitalInOut, Direction, Pull
            k = DigitalInOut(board.KEY); k.direction = Direction.INPUT; k.pull = Pull.UP
            self._k = k
            b = DigitalInOut(board.BEEP); b.direction = Direction.OUTPUT
            self._beep = b
            l = DigitalInOut(board.LED); l.direction = Direction.OUTPUT
            self._led = l
            print("[key] 板载按键/蜂鸣/LED 已就绪")
        except Exception as e:
            self._k = None
            print(f"[key] 不可用({e})")

    @property
    def pressed(self):
        return self._k is not None and self._k.value == 0

    def flash(self, led=False, dur=0.05):
        if self._beep is not None:
            self._beep.value = 1
        if led and self._led is not None:
            self._led.value = 1
        time.sleep(dur)
        if self._beep is not None:
            self._beep.value = 0
        if led and self._led is not None:
            self._led.value = 0


# ----------------------------- 模式渲染(占位)-------------------------
def render_adapter(img, key, actions):
    pass


def render_scan(img, key, actions):
    pass


def render_range(img, key, actions):
    pass


def draw_hud(img, mode, fps):
    text(img, f"[{mode + 1}/3] {MODES[mode]}", (10, 8), (0, 255, 120), 22)
    text(img, f"FPS:{fps:4.1f}", (W - 90, 8), (160, 160, 160), 20)
    text(img, "左/右切换 · 中间模式内动作", (W // 2 - 150, H - 28), (110, 110, 110), 18)


# ----------------------------- 主程序 ------------------------------
def main():
    Display.init()
    flipped = (direction.get_lcd() == 2)
    if flipped:
        Display.set_rotation(2)

    touch = TouchInput(flipped=flipped)
    key = Key()

    mode = 0
    actions = []
    prev_key = False
    fc = 0
    fps = 0.0
    fps_t = time.monotonic()

    while True:
        now = time.monotonic()

        region = touch.poll_region()
        if region in ("LEFT", "RIGHT"):
            actions.clear()
            mode = (mode + (1 if region == "RIGHT" else -1)) % len(MODES)
            key.flash(led=True, dur=0.03)
        elif region == "CENTER":
            actions.append(("tap", "CENTER"))

        if key.pressed and not prev_key:
            actions.clear()
            mode = (mode + 1) % len(MODES)
            key.flash(led=True, dur=0.03)
        prev_key = key.pressed

        img = np.zeros((H, W, 3), np.uint8)
        if mode == 0:
            render_adapter(img, key, actions)
        elif mode == 1:
            render_scan(img, key, actions)
        else:
            render_range(img, key, actions)

        draw_hud(img, mode, fps)
        Display.show(img)
        try:
            IDE_show(img)
        except Exception:
            pass

        fc += 1
        if now - fps_t >= 1.0:
            fps = fc / (now - fps_t)
            fc = 0
            fps_t = now


def IDE_show(img):
    try:
        from walnutpi import IDE
        IDE.show(img)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("退出")
```

- [ ] **Step 2: Deploy + smoke test (no crash for 6s)**

```bash
cd /Users/netseye/Documents/cybercam/CyberCAM-Apps
expect /tmp/scp.exp app/bt-showcase/main.py pi@10.10.11.213:/data/app/bt-showcase/main.py
expect /tmp/sshcmd.exp "cd /data/app/bt-showcase && timeout 6 python -u main.py 2>&1 | grep -E '\[touch\]|\[key\]|Traceback|Error'; echo DONE"
```
Expected: `[touch]` and `[key]` lines, no Traceback, `DONE`.

- [ ] **Step 3: Commit**

```bash
git add app/bt-showcase/main.py
git commit -m "feat(bt-showcase): main.py shell (Display/Touch/Key/router, no camera)"
```

---

## Task 7: M1 adapter render + discoverable toggle + refresh thread

**Files:**
- Modify: `app/bt-showcase/main.py` (add adapter state + thread + replace `render_adapter`)

- [ ] **Step 1: Add adapter state + background refresh thread**

Insert after `MODES = [...]` (before the `# --- 文字 ---` section):

```python
import btctl

# ----------------------------- M1 适配器状态 -------------------------
adapter_lock = threading.Lock()
adapter_info = {'available': False, 'mac': '', 'name': '', 'alias': '',
                'class': '', 'powered': False, 'discoverable': False,
                'pairable': False, 'discovering': False, 'uuids': []}
adapter_stop = False


def _adapter_loop():
    '''每 ~1.2s 刷新一次适配器信息(后台线程)。'''
    global adapter_info
    while not adapter_stop:
        info = btctl.adapter_info()
        with adapter_lock:
            adapter_info = info
        time.sleep(1.2)


threading.Thread(target=_adapter_loop, daemon=True).start()
```

- [ ] **Step 2: Replace the `render_adapter` stub**

```python
def render_adapter(img, key, actions):
    '''M1 适配器:显示本机蓝牙信息,中间触摸切换可发现。'''
    with adapter_lock:
        a = dict(adapter_info)
    while actions:
        kind = actions.pop(0)
        if kind[0] == "tap" and kind[1] == "CENTER" and a.get('available'):
            btctl.set_discoverable(not a.get('discoverable'))
            key.flash(led=True, dur=0.05)
    y = 60
    if not a.get('available'):
        text(img, "蓝牙不可用", (W // 2 - 90, H // 2), (0, 160, 255), 30)
        return
    text(img, f"名称   {a.get('name','')}", (20, y), (255, 255, 255), 22); y += 32
    text(img, f"MAC    {a.get('mac','')}", (20, y), (200, 200, 200), 22); y += 32
    text(img, f"Class  {a.get('class','')}", (20, y), (200, 200, 200), 22); y += 32
    text(img, f"供电   {'是' if a.get('powered') else '否'}", (20, y),
         (100, 255, 100) if a.get('powered') else (0, 160, 255), 22); y += 32
    disc = a.get('discoverable')
    text(img, f"可发现 {'开' if disc else '关'}  <- 中间切换", (20, y),
         (0, 255, 120) if disc else (0, 180, 255), 22); y += 32
    text(img, f"可配对 {'是' if a.get('pairable') else '否'}", (20, y),
         (200, 200, 200), 22); y += 32
    # 支持的 profile(最多显示 6 个 UUID)
    text(img, "支持 profile:", (20, y), (180, 180, 255), 20); y += 26
    for u in a.get('uuids', [])[:6]:
        text(img, "  " + u, (20, y), (160, 160, 160), 16); y += 20
```

- [ ] **Step 3: Deploy + smoke test**

```bash
expect /tmp/scp.exp app/bt-showcase/main.py pi@10.10.11.213:/data/app/bt-showcase/main.py
expect /tmp/sshcmd.exp "cd /data/app/bt-showcase && timeout 6 python -u main.py 2>&1 | grep -E '\[touch\]|\[key\]|Traceback|Error'; echo DONE"
```
Expected: no Traceback, `DONE`.

- [ ] **Step 4: Manual check**

Confirm M1 shows `WalnutPi` / MAC `10:11:12:13:14:15` / Class / profile list. Center-tap toggles 可发现 开/关 (verify on another device's BT scan that `WalnutPi` appears/disappears).

- [ ] **Step 5: Commit**

```bash
git add app/bt-showcase/main.py
git commit -m "feat(bt-showcase): M1 adapter info + discoverable toggle + refresh thread"
```

---

## Task 8: M2 scan render + background scan thread + device selection

**Files:**
- Modify: `app/bt-showcase/main.py` (add scan state + thread + replace `render_scan`)

- [ ] **Step 1: Add scan state + selection (after adapter state block)**

Insert after the `_adapter_loop` thread start:

```python
# ----------------------------- M2 扫描状态 ---------------------------
scan_lock = threading.Lock()
scan_devices = []          # [{'mac','name'}]
scan_state = "idle"        # 'idle' | 'scanning'
scan_page = 0
selected_mac = None        # 全局,供 M3 使用

PER_PAGE = 8


def start_scan():
    '''后台扫描一次(约 8 秒)。已扫描中则忽略。'''
    global scan_devices, scan_state
    with scan_lock:
        if scan_state == "scanning":
            return
        scan_state = "scanning"

    def work():
        global scan_devices, scan_state
        devs = btctl.scan_devices(8)
        with scan_lock:
            scan_devices = devs
            scan_state = "idle"

    threading.Thread(target=work, daemon=True).start()
```

- [ ] **Step 2: Replace the `render_scan` stub**

```python
def render_scan(img, key, actions):
    '''M2 扫描:中间触摸开始/重扫;列表分页;点选设备(selected_mac)。'''
    global scan_page, selected_mac
    with scan_lock:
        st = scan_state
        devs = list(scan_devices)
    while actions:
        kind = actions.pop(0)
        if kind[0] == "tap" and kind[1] == "CENTER":
            if st == "scanning":
                scan_page = 0
            else:
                start_scan()
                scan_page = 0
            key.flash(dur=0.03)
    text(img, "中间触摸开始/重新扫描", (10, 36), (200, 200, 200), 20)
    if st == "scanning":
        text(img, "扫描中…", (W // 2 - 50, H // 2), (0, 200, 255), 28)
        return
    if not devs:
        text(img, "无设备(中间重扫)", (W // 2 - 110, H // 2), (160, 160, 160), 22)
        return
    page, total = core.paginate(devs, scan_page, PER_PAGE)
    text(img, f"共 {len(devs)} 台  {scan_page + 1}/{total}", (W - 200, 36), (180, 180, 255), 18)
    y = 70
    for i, d in enumerate(page):
        idx = scan_page * PER_PAGE + i
        mac, name = d['mac'], d['name'][:16]
        sel = (mac == selected_mac)
        col = (0, 255, 120) if sel else (220, 220, 220)
        text(img, f"{idx + 1}. {name}", (20, y), col, 20)
        text(img, mac, (320, y), (150, 150, 150), 16)
        y += 28
    text(img, "点选设备高亮 -> 测距目标(翻页用按键)", (10, H - 50), (140, 140, 140), 16)
```

- [ ] **Step 3: Wire device selection in the main loop**

In `main()`, replace the input-handling block's CENTER branch so a CENTER tap in scan mode **selects** the current page's first device (simple, no per-row hit-testing) OR if already selected cycles to next device. Add after the existing `elif region == "CENTER": actions.append(("tap", "CENTER"))`:

```python
        elif region == "CENTER":
            if mode == 1:  # 扫描模式:点选 / 循环设备
                with scan_lock:
                    devs = list(scan_devices)
                if devs:
                    cur = next((i for i, d in enumerate(devs) if d['mac'] == selected_mac), -1)
                    nxt = devs[(cur + 1) % len(devs)]
                    selected_mac = nxt['mac']
                    key.flash(led=True, dur=0.03)
                else:
                    actions.append(("tap", "CENTER"))  # 无设备时落到 render_scan 提示重扫
            else:
                actions.append(("tap", "CENTER"))
```

- [ ] **Step 4: Deploy + smoke test**

```bash
expect /tmp/scp.exp app/bt-showcase/main.py pi@10.10.11.213:/data/app/bt-showcase/main.py
expect /tmp/sshcmd.exp "cd /data/app/bt-showcase && timeout 8 python -u main.py 2>&1 | grep -E 'Traceback|Error'; echo DONE"
```
Expected: no Traceback, `DONE`.

- [ ] **Step 5: Manual check**

Switch to M2, center-tap to scan (~8s), see device list. Center-tap cycles selection (highlight turns green).

- [ ] **Step 6: Commit**

```bash
git add app/bt-showcase/main.py
git commit -m "feat(bt-showcase): M2 scan + background thread + device selection"
```

---

## Task 9: M3 range render + background ping thread + bar graph

**Files:**
- Modify: `app/bt-showcase/main.py` (add ping state + thread + replace `render_range`)

- [ ] **Step 1: Add ping state + background worker (after scan state block)**

```python
# ----------------------------- M3 测距状态 ---------------------------
ping_lock = threading.Lock()
ping_hist = deque(maxlen=40)   # 每项 {'ok':bool,'ms':float|None}
ping_stop = False


def _ping_loop():
    '''按 selected_mac 每秒 l2ping 一次,推入历史。'''
    while not ping_stop:
        mac = selected_mac
        if mac:
            res = btctl.l2ping_once(mac, timeout=3)
            with ping_lock:
                ping_hist.append(res)
        time.sleep(1.0)


threading.Thread(target=_ping_loop, daemon=True).start()
```

- [ ] **Step 2: Replace the `render_range` stub**

```python
def render_range(img, key, actions):
    '''M3 测距:对 selected_mac 反复 l2ping,画延迟/可达柱状图。'''
    with scan_lock:
        devs = list(scan_devices)
    name = next((d['name'] for d in devs if d['mac'] == selected_mac), None)
    while actions:
        kind = actions.pop(0)
        if kind[0] == "tap" and kind[1] == "CENTER":
            with ping_lock:
                ping_hist.clear()
            key.flash(dur=0.03)
    if not selected_mac:
        text(img, "先在扫描模式选中设备", (W // 2 - 140, H // 2), (0, 180, 255), 24)
        return
    with ping_lock:
        hist = list(ping_hist)
    text(img, f"目标 {name or selected_mac}", (10, 36), (255, 255, 255), 20)
    text(img, selected_mac, (10, 64), (150, 150, 150), 16)
    text(img, "中间清空历史", (W - 160, 36), (160, 160, 160), 16)

    if not hist:
        text(img, "测量中…", (W // 2 - 60, H // 2), (0, 200, 255), 24)
        return

    ok_n = sum(1 for h in hist if h['ok'])
    rate = 100.0 * ok_n / len(hist)
    last = hist[-1]
    if last['ok']:
        text(img, f"最近 {last['ms']:.1f} ms   可达率 {rate:.0f}%",
             (10, 92), (0, 255, 120), 22)
    else:
        text(img, f"无响应/不可达   可达率 {rate:.0f}%", (10, 92), (0, 160, 255), 22)

    # 柱状图:每条宽 12,高按 ms(上限 200ms 映射到 120px),失败画矮红条
    bx, by = 20, 300
    for i, h in enumerate(hist):
        x = bx + i * 13
        if h['ok']:
            hh = int(min(120, max(4, h['ms'] / 200.0 * 120)))
            cv2.rectangle(img, (x, by + 120 - hh), (x + 10, by + 120), (0, 200, 80), -1)
        else:
            cv2.rectangle(img, (x, by + 110), (x + 10, by + 120), (0, 80, 220), -1)
    cv2.line(img, (bx, by + 120), (bx + len(hist) * 13, by + 120), (120, 120, 120), 1)
    text(img, "绿=可达(高=延迟大)  红=无响应", (10, by + 132), (150, 150, 150), 16)
```

- [ ] **Step 3: Deploy + smoke test**

```bash
expect /tmp/scp.exp app/bt-showcase/main.py pi@10.10.11.213:/data/app/bt-showcase/main.py
expect /tmp/sshcmd.exp "cd /data/app/bt-showcase && timeout 8 python -u main.py 2>&1 | grep -E 'Traceback|Error'; echo DONE"
```
Expected: no Traceback, `DONE`.

- [ ] **Step 4: Manual check**

Select a device in M2, switch to M3. If the peer responds to l2ping you see green bars + latency; otherwise red bars + "无响应". This is correct behavior (many consumer devices ignore L2CAP echo).

- [ ] **Step 5: Commit**

```bash
git add app/bt-showcase/main.py
git commit -m "feat(bt-showcase): M3 range test (l2ping worker + bar graph)"
```

---

## Task 10: Icon + README

**Files:**
- Create: `app/bt-showcase/icon.png`, `app/bt-showcase/README.md`

- [ ] **Step 1: Generate icon.png on device**

Write `/tmp/genicon_bt.py`:
```python
import cv2, math, numpy as np
ft = cv2.freetype.createFreeType2(); ft.loadFontData("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0)
S = 256
img = np.zeros((S, S, 3), np.uint8)
for y in range(S):
    img[y, :] = (int(20 + 35 * y / S), int(15 + 25 * y / S), int(40 + 45 * y / S))
cv2.circle(img, (128, 116), 72, (30, 30, 55), -1)
cv2.circle(img, (128, 116), 72, (190, 220, 255), 3)
# 蓝牙符号:竖线 + 两个三角
cv2.line(img, (128, 60), (128, 172), (255, 230, 90), 6)
cv2.line(img, (128, 60), (104, 88), (255, 230, 90), 6)
cv2.line(img, (128, 60), (152, 88), (255, 230, 90), 6)
cv2.line(img, (104, 88), (152, 144), (255, 230, 90), 0)  # 占位,不影响
ft.putText(img=img, text="蓝牙", org=(78, 224), fontHeight=40, color=(240, 255, 245),
           thickness=-1, line_type=cv2.LINE_AA, bottomLeftOrigin=False)
cv2.imwrite("/tmp/icon.png", img)
print("icon saved")
```
Run + pull:
```bash
expect /tmp/scp.exp /tmp/genicon_bt.py pi@10.10.11.213:/tmp/genicon_bt.py
expect /tmp/sshcmd.exp "python /tmp/genicon_bt.py"
expect /tmp/scp.exp pi@10.10.11.213:/tmp/icon.png app/bt-showcase/icon.png
```

- [ ] **Step 2: Write README.md**

`app/bt-showcase/README.md`:
```markdown
# 蓝牙展示 (BT Showcase)

CyberCAM 上的蓝牙能力展示 app,三模式:**适配器信息 / 扫描周边 / 测距(l2ping)**。

## 三种模式

| 模式 | 内容 |
| --- | --- |
| 1️⃣ 适配器 | 本机蓝牙名称/MAC/Class/供电/可发现/可配对/支持 profile。中间触摸切换可发现。 |
| 2️⃣ 扫描 | 扫描周边蓝牙设备(名称/MAC)。中间触摸开始/重扫;点选设备作为测距目标。 |
| 3️⃣ 测距 | 对选中设备反复 l2ping,显示最近延迟、可达率与柱状图。中间清空历史。 |

## 交互

- **触摸左/右**:上一/下一模式;**触摸中间**:模式内动作(切换可发现/扫描/选设备/清空)。
- **板载按键 KEY**:下一模式。

## 为什么这样设计

- 走 `bluetoothctl`(BlueZ D-Bus,**非 root**);只有 `l2ping` 用 `sudo`(pi 免密)。
- **不做双向数据(SPP)**:本机 BlueZ 5 无 `--compat`,`sdptool` 失败、`rfcomm` 不建 `/dev/rfcommN`,SPP 跑不通(已验证)。`dbus-python`/`pybluez` 也未安装。
- **测距说明**:l2ping 走 L2CAP echo,许多消费电子(电视/空调)不响应 → 显示"无响应/不可达"是正确结果;对手机/Linux 蓝牙设备会显示真实延迟。
- **不开摄像头**:保持轻量,避免抢占 `/dev/video`。

## 文件

- `core.py` — 纯解析逻辑(parse_show/parse_devices/parse_l2ping/paginate),PC 可单测。
- `btctl.py` — subprocess 封装(adapter_info/scan_devices/set_discoverable/l2ping_once)。
- `main.py` — Display/Touch/Key/模式路由 + 3 渲染 + 后台线程。

## 部署

拷到 `/data/app/bt-showcase/` 即可在系统 GUI 看到图标。调试:

    cd /data/app/bt-showcase && python main.py    # Ctrl-C 退出
```

- [ ] **Step 3: Commit**

```bash
git add app/bt-showcase/icon.png app/bt-showcase/README.md
git commit -m "feat(bt-showcase): icon + README"
```

---

## Task 11: Integration regression + deploy + clean PR branch

- [ ] **Step 1: Full device deploy**

```bash
cd /Users/netseye/Documents/cybercam/CyberCAM-Apps
for f in app.txt run.sh core.py btctl.py main.py icon.png README.md; do
  expect /tmp/scp.exp "app/bt-showcase/$f" "pi@10.10.11.213:/data/app/bt-showcase/$f"
done
expect /tmp/sshcmd.exp "chmod +x /data/app/bt-showcase/run.sh && ls /data/app/bt-showcase/"
```

- [ ] **Step 2: Final smoke (10s, all subsystems init, no crash)**

```bash
expect /tmp/sshcmd.exp "cd /data/app/bt-showcase && timeout 10 python -u main.py 2>&1 | grep -E '\[touch\]|\[key\]|Traceback|Error'; echo DONE"
```
Expected: `[touch]` + `[key]` lines, no Traceback, `DONE`.

- [ ] **Step 3: Local unit tests still green**

```bash
python3 app/bt-showcase/tests/test_parse_show.py && python3 app/bt-showcase/tests/test_parse_devices.py && python3 app/bt-showcase/tests/test_parse_l2ping.py
```
Expected: `OK` three times.

- [ ] **Step 4: Manual regression**

- M1: shows WalnutPi/MAC/Class/profiles; center-tap toggles 可发现 (verify on phone).
- M2: scan returns device list; center-tap selects one.
- M3: shows ping results (green if responsive, red if not).

- [ ] **Step 5: Prepare clean app-only PR branch**

```bash
cd /Users/netseye/Documents/cybercam/CyberCAM-Apps
git checkout main
git checkout -b add-bt-showcase-pr
git checkout add-bt-showcase -- app/bt-showcase
# 排除 __pycache__
git rm -r --cached app/bt-showcase/__pycache__ 2>/dev/null || true
printf '__pycache__/\n*.pyc\n' > app/bt-showcase/.gitignore
git add app/bt-showcase
git commit -m "新增 bt-showcase: 蓝牙展示 app（适配器/扫描/l2ping 测距）"
```

---

## Self-Review (completed)

- **Spec coverage:** M1 适配器 → T7; M2 扫描 → T8; M3 测距 → T9; 纯解析层(core.py) → T2/T3/T4; subprocess 封装(btctl.py) → T5; 外壳(Display/Touch/Key/路由) → T6; 文件结构/app 规范 → T1/T10; 并发(后台线程) → T7/T8/T9; 健壮性(bluetoothd 不可用/超时/无响应) → T2(parse_show available=False)/T5(_run 吞异常)/T9(无响应红条); 测试(Mac 单测 + 设备冒烟 + 人工) → 各任务 Step; PR-clean → T11。
- **关键修正(相对 spec):** spec 写了 `paired-devices`,实测该版本 bluetoothctl **无效**,已从实现中移除(M1 只用 `show`)。spec 提到 RSSI,实测 l2ping 更可靠作为测距手段,RSSI 留作后续(YAGNI)。
- **Placeholder 扫描:** 无 —— 每步都有完整代码或确切命令 + 预期输出。
- **类型一致性:** `parse_show` dict 键(`available/mac/name/alias/class/powered/discoverable/pairable/discovering/uuids`)在 T2 定义、T5 `adapter_info` 返回、T7 `render_adapter` 读取,完全一致;`parse_devices` 返回 `[{'mac','name'}]`,T8 `render_scan` 读取 `d['mac']/d['name']` 一致;`parse_l2ping` 返回 `{'ok','ms'}`,T9 读取 `h['ok']/h['ms']` 一致;`selected_mac` 全局在 T8 写、T9 读,一致;`paginate` 签名 `(items, page, per_page) -> (list, total)` 在 T3/T8 一致。
- **已知限制:** l2ping 对多数消费设备无响应(已验证),M3 因此常显示"无响应";这是正确行为,README 已说明。
