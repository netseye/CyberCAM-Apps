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
        mm = re.search(r'^\t?' + key + r':\s*(.+)$', text, re.M | re.I)
        if mm:
            out[key] = mm.group(1).strip()
    out['powered'] = _line_bool(text, 'Powered')
    out['discoverable'] = _line_bool(text, 'Discoverable')
    out['pairable'] = _line_bool(text, 'Pairable')
    out['discovering'] = _line_bool(text, 'Discovering')
    for mm in re.finditer(r'UUID:\s+.*\(([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\)', text):
        out['uuids'].append(mm.group(1))
    return out


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
