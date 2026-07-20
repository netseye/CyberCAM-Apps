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
