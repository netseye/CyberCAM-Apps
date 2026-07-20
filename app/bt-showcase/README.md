# 蓝牙展示 (BT Showcase)

CyberCAM 上的蓝牙能力展示 app,三模式:**适配器信息 / 扫描周边 / 测距(l2ping)**。仓库里第一个蓝牙 app,且不开摄像头。

## 三种模式

| 模式 | 内容 |
| --- | --- |
| 1️⃣ 适配器 | 本机蓝牙名称/MAC/Class/供电/可发现/可配对/支持 profile。中间触摸切换可发现(后台每 ~1.2s 刷新真实状态)。 |
| 2️⃣ 扫描 | 扫描周边蓝牙设备(名称/MAC)。中间触摸:无设备时开始扫描,有设备时点选(循环高亮,自动翻页)作为测距目标。 |
| 3️⃣ 测距 | 对选中设备反复 l2ping,显示最近延迟、可达率与柱状图(绿=可达,红=无响应)。中间清空历史。 |

## 交互

- **触摸左/右**:上一/下一模式;**触摸中间**:模式内动作(切换可发现/扫描或点选/清空)。
- **板载按键 KEY**:下一模式。

## 为什么这样设计

- 走 `bluetoothctl`(BlueZ D-Bus,**非 root**);只有 `l2ping` 用 `sudo`(pi 免密)。
- **不做双向数据(SPP)**:本机 BlueZ 5 无 `--compat`,`sdptool` 失败、`rfcomm` 不建 `/dev/rfcommN`,SPP 跑不通(已验证)。`dbus-python`/`pybluez` 也未安装。
- **不开摄像头**:保持轻量,避免抢占 `/dev/video`。

## 已知限制

- **测距**:l2ping 走 L2CAP echo,许多消费电子(电视/空调)不响应 → 显示"无响应/不可达"是正确结果;对手机/Linux 蓝牙设备会显示真实延迟。
- **重新扫描**:M2 首次扫描后,中间触摸用于点选目标(不再触发重扫);如需扫描新出现的设备,重启 app 即可(首次 8 秒扫描覆盖周边)。
- 触摸区域按屏幕左/中/右三段判别;触摸不可用时仍可用板载按键切模式。

## 依赖(设备自带)

- Python 3 + `opencv-python`(含 `cv2.freetype`)+ `numpy` + `walnutpi`
- 中文字体 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`
- BlueZ `bluetoothctl`(D-Bus,非 root)+ `l2ping`(sudo,pi 免密)
- 触摸屏 `/dev/input/event0` + 板载 `board.KEY/LED/BEEP`(缺失时自动降级)

## 文件

- `app.txt` / `run.sh` — 系统启动器注册(`index=22`)与入口。
- `core.py` — 纯解析逻辑(parse_show/parse_devices/parse_l2ping/paginate),PC 可单测。
- `btctl.py` — subprocess 封装(adapter_info/scan_devices/set_discoverable/l2ping_once)。
- `main.py` — Display/Touch/Key/模式路由 + 3 渲染 + 后台线程。

## 部署

拷到 `/data/app/bt-showcase/` 即可在系统 GUI 看到图标。调试:

    cd /data/app/bt-showcase && python main.py    # Ctrl-C 退出
