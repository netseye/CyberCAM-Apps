# 本地语音唤醒资源

App 已内置以下资源，部署时随 `/data/app/xiaozhi/wake` 一起复制，不替换系统动态库：

需要修改关键词时，请参阅[修改离线唤醒词](../WAKE_WORD.md)。

- `runtime-spacemit/`：sherpa-onnx v1.13.2 `linux-riscv64-spacemit-shared`
- `model/`：`sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`
- `keywords.txt`：本 App 的“小智小智”拼音 token、增益和触发阈值
- `native/wakeword-daemon`：常驻模型进程；对话时只暂停采集并释放麦克风，模型不卸载
- `parent_guard.py`：监控 App 父进程；即使 App 异常退出也会终止唤醒进程并释放麦克风
- `manifest.sha256`：内置二进制、运行库和模型的完整性清单

K230 C908 需要 SpaceMi/RVV 构建，并显式指定 `model-type=zipformer2`。通用 RISC-V 包携带 RVV 0.7/T-Head loader，在该系统上不可直接使用。

部署时复制完整 App 目录即可，不再需要运行安装脚本：

```sh
./app/xiaozhi/deploy.sh 10.10.11.213
```

维护者修改 `native/wakeword_daemon.cpp` 后，可在 K230 上重新构建并更新内置文件：

```sh
g++ -O2 -std=c++17 wake/native/wakeword_daemon.cpp \
  -Lwake/runtime-spacemit/lib -lsherpa-onnx-c-api -lasound -pthread \
  -Wl,-rpath,'$ORIGIN/../runtime-spacemit/lib' \
  -o wake/native/wakeword-daemon
```

提交前在 `app/xiaozhi` 目录运行 `sha256sum -c wake/manifest.sha256` 校验资源。

模型只在 App 启动时预热一次。之后每轮对话结束只需重新打开 ALSA 采集，避免重复等待模型加载。
