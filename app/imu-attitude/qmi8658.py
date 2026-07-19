'''
qmi8658.py — CyberCAM 上 QMI8658A(6轴:加速度+陀螺仪)的纯 Python I2C 驱动。

设计要点:
- 只用 Python 标准库(fcntl/os/struct/time),不依赖 smbus / i2c-tools。
- 走 /dev/i2c-1,从机地址 0x6a。WHO_AM_I(0x00)=0x05 校验。
- 配置:CTRL1=0x60(自动增量) CTRL2=0x23(±2g) CTRL3=0x43 CTRL7=0x03(使能)。
- 从 0x35 连读 12 字节 = ax,ay,az,gx,gy,gz(int16 小端)。
- 换算:加速度 /4096 -> g;陀螺仪 /64 -> dps。
- 陀螺仪零偏校准:静止采样求均值后扣除。
- open_imu() 工厂:I2C 不可用或芯片不符时,退回到系统 QMA6100P(仅加速度)。
'''

import fcntl
import os
import struct
import time

I2C_SLAVE = 0x0703

# QMI8658A 寄存器
REG_WHOAMI = 0x00
CTRL1, CTRL2, CTRL3, CTRL7 = 0x02, 0x03, 0x04, 0x08
ACC_START = 0x35  # 连读 12 字节: ax,ay,az,gx,gy,gz

# 换算系数(对应 CTRL2=0x23 / CTRL3=0x43)
ACC_LSB = 4096.0   # raw / ACC_LSB = g
GYR_LSB = 64.0     # raw / GYR_LSB = dps


class QMI8658:
    '''直接 I2C 驱动 QMI8658A,输出 (ax,ay,az) 单位 g,(gx,gy,gz) 单位 dps。'''

    def __init__(self, bus=1, addr=0x6a):
        self.bus = bus
        self.addr = addr
        self.fd = os.open(f'/dev/i2c-{bus}', os.O_RDWR)
        self._set_slave(addr)
        who = self._read_reg(REG_WHOAMI, 1)
        if who != b'\x05':
            raise OSError(f'WHO_AM_I 异常: 0x{who.hex()} (期望 0x05)')
        # 配置:自动增量 + ±2g + 使能加速度与陀螺仪
        self._write_reg(CTRL1, 0x60)
        self._write_reg(CTRL2, 0x23)
        self._write_reg(CTRL3, 0x43)
        self._write_reg(CTRL7, 0x03)
        self._gbias = (0.0, 0.0, 0.0)  # 陀螺仪零偏(dps)
        time.sleep(0.05)               # 等第一帧有效

    # ---- 底层 I2C ----
    def _set_slave(self, addr):
        fcntl.ioctl(self.fd, I2C_SLAVE, addr)

    def _write_reg(self, reg, val):
        self._set_slave(self.addr)
        os.write(self.fd, bytes([reg & 0xFF, val & 0xFF]))

    def _read_reg(self, reg, n):
        self._set_slave(self.addr)
        os.write(self.fd, bytes([reg & 0xFF]))
        return os.read(self.fd, n)

    # ---- 公开接口 ----
    has_gyro = True
    chip_name = 'QMI8658A'

    def read(self):
        raw = self._read_reg(ACC_START, 12)
        if len(raw) < 12:
            return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        ax, ay, az = [v / ACC_LSB for v in struct.unpack('<hhh', raw[0:6])]
        gx, gy, gz = [v / GYR_LSB for v in struct.unpack('<hhh', raw[6:12])]
        gx -= self._gbias[0]
        gy -= self._gbias[1]
        gz -= self._gbias[2]
        return (ax, ay, az, gx, gy, gz)

    def calibrate(self, n=100, quiet=False):
        '''静止采样 n 次,取陀螺仪三轴均值作为零偏。'''
        sx = sy = sz = 0.0
        m = 0
        for _ in range(n):
            try:
                _, _, _, gx, gy, gz = self.read()
                sx += gx; sy += gy; sz += gz; m += 1
            except OSError:
                pass
            time.sleep(0.005)
        if m:
            self._gbias = (sx / m, sy / m, sz / m)
        if not quiet:
            print(f'[qmi8658] 陀螺仪零偏(dps): {self._gbias}')


class _AccelOnly:
    '''Fallback:系统 QMA6100P,只读加速度(无陀螺仪)。'''

    has_gyro = False
    chip_name = 'QMA6100P'

    def __init__(self):
        from walnutpi_imu import imu as _imu  # 延迟导入
        self._imu = _imu

    def read(self):
        ax, ay, az = self._imu.get_lcd()
        return (float(ax), float(ay), float(az), 0.0, 0.0, 0.0)

    def calibrate(self, n=100, quiet=False):
        if not quiet:
            print('[imu] 仅加速度模式,无需陀螺仪校准')


def open_imu(bus=1, addr=0x6a):
    '''优先 QMI8658A;I2C 不可用或校验失败时退回 QMA6100P 仅加速度。'''
    try:
        dev = QMI8658(bus=bus, addr=addr)
        print(f'[imu] 使用 {dev.chip_name} (bus{bus} 0x{addr:02x})')
        return dev
    except (OSError, PermissionError) as e:
        print(f'[imu] QMI8658A 不可用({e}),退回系统 QMA6100P 仅加速度')
        return _AccelOnly()


if __name__ == '__main__':
    # 自测:打印 1 秒数据
    d = open_imu()
    d.calibrate(quiet=True)
    for _ in range(10):
        print('  a[g]=%+6.3f %+6.3f %+6.3f   g[dps]=%+7.2f %+7.2f %+7.2f' % d.read())
        time.sleep(0.1)
