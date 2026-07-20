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
