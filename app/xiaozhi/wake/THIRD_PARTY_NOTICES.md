# 第三方唤醒资源

本目录内置的 sherpa-onnx 运行库和关键词模型仅用于小智的本地离线唤醒。

以下两项资源均按 [Apache License 2.0](./APACHE-2.0.txt) 再分发：

- sherpa-onnx v1.13.2 `linux-riscv64-spacemit-shared`：来源于 [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)。原始发布包 SHA-256：`4c2c101da444dcca72274653ada16edf2c4b009e55a57bc0e5ef6337f4dbfc60`。
- `sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`：来源于 sherpa-onnx KWS 模型发布包。原始发布包 SHA-256：`b2f7c89690dc8ce4c6ed6afeab7cd800c36ad1421fb6b6302b4a4b194cf7f35f`。

仓库只保留运行小智所需的最小文件集合。`native/wakeword-daemon` 由同目录下的 `wakeword_daemon.cpp` 在 CyberCAM K230 上编译，使用内置的 sherpa-onnx C API；官方 `sherpa-onnx-keyword-spotter-alsa` 仅作为常驻服务不可用时的降级路径。
