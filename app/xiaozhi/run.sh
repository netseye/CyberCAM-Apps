#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$APP_DIR"

required_assets="
wake/native/wakeword-daemon
wake/runtime-spacemit/bin/sherpa-onnx-keyword-spotter-alsa
wake/runtime-spacemit/lib/libsherpa-onnx-c-api.so
wake/runtime-spacemit/lib/libonnxruntime.so.1
wake/runtime-spacemit/lib/libonnxruntime_providers_shared.so
wake/runtime-spacemit/lib/libspacemit_ep.so.2
wake/model/tokens.txt
wake/model/encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx
wake/model/decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx
wake/model/joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx
wake/keywords.txt
"

for asset in $required_assets; do
    if [ ! -s "$asset" ]; then
        echo "错误：小智内置唤醒资源缺失：$asset" >&2
        echo "请重新复制完整的 app/xiaozhi 目录" >&2
        exit 1
    fi
done

# 某些上传工具不会保留可执行位，启动时自动修复，无需在设备上编译。
chmod 755 \
    wake/native/wakeword-daemon \
    wake/runtime-spacemit/bin/sherpa-onnx-keyword-spotter-alsa

exec python -u main.py
