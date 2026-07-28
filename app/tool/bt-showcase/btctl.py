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
