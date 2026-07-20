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
