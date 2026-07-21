# 本地语音唤醒资源

设备部署使用以下官方资源，均仅放在 `/data/app/xiaozhi/wake`，不替换系统动态库：

- `runtime-spacemit/`：sherpa-onnx v1.13.2 `linux-riscv64-spacemit-shared`
- `model/`：`sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`
- `keywords.txt`：本 App 的“小智小智”拼音 token、增益和触发阈值
- `native/wakeword-daemon`：常驻模型进程；对话时只暂停采集并释放麦克风，模型不卸载
- `parent_guard.py`：监控 App 父进程；即使 App 异常退出也会终止唤醒进程并释放麦克风
- `install.sh`：在 K230 上下载固定版本资源、校验 SHA-256 并编译常驻进程

K230 C908 需要 SpaceMi/RVV 构建，并显式指定 `model-type=zipformer2`。通用 RISC-V 包携带 RVV 0.7/T-Head loader，在该系统上不可直接使用。

首次部署或需要重新安装全部资源：

```sh
cd /data/app/xiaozhi
./wake/install.sh
```

仅重新编译常驻进程：

```sh
g++ -O2 -std=c++17 wake/native/wakeword_daemon.cpp \
  -Lwake/runtime-spacemit/lib -lsherpa-onnx-c-api -lasound -pthread \
  -Wl,-rpath,'$ORIGIN/../runtime-spacemit/lib' \
  -o wake/native/wakeword-daemon
```

模型只在 App 启动时预热一次。之后每轮对话结束只需重新打开 ALSA 采集，避免重复等待模型加载。
