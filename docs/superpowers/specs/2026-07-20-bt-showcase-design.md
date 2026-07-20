# 蓝牙展示 (BT Showcase) App — 设计

## 背景与目标

CyberCAM(K230, WalnutPi Linux)有一颗**经典蓝牙双模**射频(`hci0`, UART, BR/EDR + LE),BlueZ 守护进程 `bluetoothd` 已在运行。但仓库里**没有任何蓝牙 app**,且 Python 侧**没有 dbus-python / pybluez / bleak**,只有 `pyserial`。

本任务:新增仓库第一个蓝牙 app「蓝牙展示」,在 640×480 触摸屏上展示本机蓝牙能力 + 交互(扫描周边、测距)。一切基于 `bluetoothctl`(走 D-Bus、非 root),只有 `l2ping` 需要 `sudo`(已确认 pi 免密 sudo)。

## 已验证的设备事实(实现依据)

- `hci0`:BR/EDR + LE 双模,UP RUNNING,默认名 `WalnutPi`,MAC `10:11:12:13:14:15`,Manufacturer 2875,Class `0x00400000`。
- `bluetoothd`(PID 不定)在跑,**无 `--compat`** 标志。
- `bluetoothctl`(D-Bus,非 root)可用且实测有效:`show`、`discoverable on/off`、`pairable on/off`、`scan on`、`devices`、`paired-devices`、agent。
- `hcitool` / `hciconfig` / `l2ping` / `rfcomm` / `sdptool` 二进制都在,但**需 `CAP_NET_RAW`(root)**。
- **SPP 串口服务跑不通**:`sdptool add` 返回 255(无 `--compat`)、`rfcomm -r listen /dev/rfcomm0 N` 不创建设备节点。→ 双向数据(SPP)不在本项目范围。
- `l2ping`:`sudo l2ping -c 1 -s 10 <MAC>` 可用(免密 sudo)。
- Python:`pyserial` 有;无 dbus-python/pybluez/bleak。
- 外壳模式照搬 fun-cam / imu-attitude:`Display` + freetype 中文(`bottomLeftOrigin=False`)、`TouchInput`(/dev/input/event0,EVIOCGABS ioctl)、`Key`(board.KEY/LED/BEEP via digitalio)、`direction.get_lcd()==2` 翻转处理。
- **本 app 不开摄像头**(BT 展示不需要,且避开 `/dev/video` 与其它 app 抢占)。

## 三模式

复用 fun-cam 多模式外壳:触摸左/右 = 上一/下一模式,触摸中间 = 模式内动作,板载 KEY = 下一模式。顶部 HUD `[n/3] 模式名 · FPS`。

### M1 适配器(Adapter Info & Control)
- 解析 `bluetoothctl show` → 屏幕显示:控制器 MAC、Name/Alias、Class、Powered、Discoverable、Pairable、以及支持的 profile(UUID 列表截断显示)。
- 中间触摸:切换 **Discoverable** on/off(`bluetoothctl discoverable on/off`),切换时短闪 + 蜂鸣反馈。
- 每 ~1s 后台刷新一次,让开关状态实时反映(避免与子进程竞争:解析在一个后台 tick 里完成)。

### M2 扫描(Discovery)
- 中间触摸:启动一次扫描——后台线程跑 `bluetoothctl --timeout 8 scan on`(或 `scan on` → sleep → `devices` → `scan off`),约 8 秒。
- 扫描中 UI 显示"扫描中…(n 台)";结束后列已发现设备(名称 / MAC,RSSI 尽量取)。
- 列表超出一屏分页(触摸中间翻页 / 或自动滚动)。再次中间触摸 = 重新扫描。
- **点选某设备高亮 → 记为 `selected_mac`**,作为 M3 测距目标。显示设备数量 + 上次刷新时间。

### M3 测距(Range / Ping)
- 取 `selected_mac`(M2 选中);没有则提示"先在扫描模式选中设备"。
- 后台工作线程每 ~1s 跑一次 `sudo l2ping -c 1 -s 10 <MAC>`,解析延迟(ms)与成功/失败,推入环形缓冲(最近 ~40 次)。
- 屏幕显示:选中设备名/MAC、最近一次延迟、成功率、最近 N 次的小柱状图(绿=可达,红=失败)、连续不可达计数。
- 中间触摸:清空历史 / 重选。

## 文件结构(本地 `app/bt-showcase/`,部署到设备同名路径)

```
bt-showcase/
  app.txt        name_cn="蓝牙展示" name_en="BT Showcase" index=22
  run.sh         python main.py
  core.py        纯解析逻辑(parse_show/parse_devices/parse_l2ping),Mac 可单测
  btctl.py       subprocess 封装(adapter_info/scan_devices/l2ping_once),设备专用
  main.py        Display/Touch/Key/模式路由 + 3 渲染函数 + 后台线程
  icon.png       设备上用 cv2 生成 256x256
  README.md      简述用法
```

## 模块接口

### `core.py`(纯逻辑,无 cv2/walnutpi/subprocess)
- `parse_show(text: str) -> dict`:解析 `bluetoothctl show` 全文 → `{'mac','name','alias','class','powered','discoverable','pairable','uuids':[...]}`。容错:缺字段给默认值。
- `parse_devices(text: str) -> list[dict]`:解析 `bluetoothctl devices` 的 `Device <MAC> <Alias>` 行 → `[{'mac','name'}, ...]`。
- `parse_l2ping(text: str) -> dict|None`:解析 l2ping 输出 → 成功时 `{'ok':True,'ms':float}`;失败/无输出 `{'ok':False,'ms':None}`。
- `paginate(items, page, per_page) -> (slice, total_pages)`:列表分页纯函数。

### `btctl.py`(subprocess 封装,设备专用)
- `adapter_info(timeout=3) -> dict`:跑 `bluetoothctl show`,交给 `core.parse_show`。bluetoothd 不可用时返回带 `available=False` 的默认 dict。
- `scan_devices(seconds=8) -> list[dict]`:后台跑 scan,返回 `core.parse_devices(devices输出)`。
- `l2ping_once(mac, timeout=4) -> dict`:`sudo l2ping -c 1 -s 10 <mac>`,交给 `core.parse_l2ping`。
- 全部带 timeout,绝不挂死;捕获 `subprocess.TimeoutExpired`/`FileNotFoundError`。

### `main.py`
- 复用 fun-cam 的 `text()` / `TouchInput` / `Key`(从 fun-cam 拷贝精简,去掉摄像头/光绘相关)。
- 全局状态:`selected_mac=None`、各模式的后台结果容器 + 锁。
- 后台线程:扫描线程(M2 触发)、ping 线程(M3 常驻,按 selected_mac 工作)、适配器刷新 tick(M1)。
- 主循环:读输入 → 路由到 `render_adapter` / `render_scan` / `render_range` → `draw_hud` → `Display.show`(+`IDE.show`)。

## 并发与健壮性

- 所有阻塞 IO(scan / l2ping / show)在后台线程,主循环只读共享结果(用 `threading.Lock` 保护 deque/dict)。
- bluetoothd 不在 / hci0 缺失:M1 显示"蓝牙不可用",M2/M3 优雅降级(扫描返回空、ping 显示不可用)。
- l2ping 失败或 sudo 不可用 → 该次标记失败,UI 显示红色;不中断。
- subprocess 全部带 timeout;`bluetoothctl` 非交互式调用用 `--timeout` 或发 `quit`/`exit` 确保退出。

## 测试策略

- **core.py(TDD,Mac)**:用样本文本(真实 `bluetoothctl show` / `devices` / `l2ping` 输出片段)写断言,纯函数,无设备依赖。
- **btctl + main(设备)**:冒烟跑 `timeout 8 python main.py` 不崩、三模式渲染;`scan_devices` 在真实环境返回 list;`l2ping_once` 对一个真实手机 MAC 返回可解析 dict。
- **人工**:M1 开关可发现 → 另一设备 BT 扫描里能看到 `WalnutPi`;M2 扫到用户手机;M3 ping 真实手机 MAC 看延迟柱状图变化。

## 取舍与注意

- **不做双向数据**:SPP 在本机跑不通(已验证);BLE GATT 外设需 dbus-python(离线装不稳)→ 超出"例子"范围。BLE 双模能力仅在 M1 信息中展示。
- **不开摄像头**:保持轻量,避免抢占 `/dev/video`。
- l2ping 用 `sudo`:依赖 pi 免密 sudo(已确认 NOPASS)。若部署环境改了 sudo 策略,需在 README 注明。
- `bluetoothctl` 非交互调用注意必须让它退出(避免遗留进程);优先 `bluetoothctl --timeout N <cmd>` 一次性命令。
- 遵循仓库约定:无调试残留打印;README 写清"为什么基于 bluetoothctl 而非 SPP/pybluez"。
