#!/bin/sh
set -eu

# Install the pinned K230 wake-word runtime/model and build the persistent
# helper. Run this on the CyberCAM target from any working directory.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR=$(dirname "$SCRIPT_DIR")
RUNTIME_VERSION=1.13.2
RUNTIME_NAME="sherpa-onnx-v${RUNTIME_VERSION}-linux-riscv64-spacemit-shared"
RUNTIME_ARCHIVE="${RUNTIME_NAME}.tar.bz2"
RUNTIME_SHA256=4c2c101da444dcca72274653ada16edf2c4b009e55a57bc0e5ef6337f4dbfc60
RUNTIME_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/v${RUNTIME_VERSION}/${RUNTIME_ARCHIVE}"

MODEL_NAME=sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01
MODEL_ARCHIVE="${MODEL_NAME}.tar.bz2"
MODEL_SHA256=b2f7c89690dc8ce4c6ed6afeab7cd800c36ad1421fb6b6302b4a4b194cf7f35f
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/${MODEL_ARCHIVE}"

if [ "$(uname -m)" != "riscv64" ]; then
  echo "错误：该安装脚本只能在 CyberCAM riscv64 设备上运行" >&2
  exit 1
fi

for command in tar sha256sum g++; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "错误：缺少命令 $command" >&2
    exit 1
  fi
done

if command -v curl >/dev/null 2>&1; then
  download() { curl -L --fail --retry 3 -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  download() { wget -O "$2" "$1"; }
else
  echo "错误：需要 curl 或 wget 下载官方资源" >&2
  exit 1
fi

TEMP_DIR=$(mktemp -d /tmp/xiaozhi-wake-install.XXXXXX)
trap 'rm -rf "$TEMP_DIR"' EXIT HUP INT TERM

download_and_verify() {
  url=$1
  output=$2
  expected=$3
  download "$url" "$output"
  printf '%s  %s\n' "$expected" "$output" | sha256sum -c -
}

echo "下载并校验 sherpa-onnx ${RUNTIME_VERSION} SpaceMi 运行库…"
download_and_verify "$RUNTIME_URL" "$TEMP_DIR/$RUNTIME_ARCHIVE" "$RUNTIME_SHA256"
tar -xjf "$TEMP_DIR/$RUNTIME_ARCHIVE" -C "$TEMP_DIR"

echo "下载并校验中文关键词模型…"
download_and_verify "$MODEL_URL" "$TEMP_DIR/$MODEL_ARCHIVE" "$MODEL_SHA256"
tar -xjf "$TEMP_DIR/$MODEL_ARCHIVE" -C "$TEMP_DIR"

mkdir -p "$SCRIPT_DIR/runtime-spacemit" "$SCRIPT_DIR/model" "$SCRIPT_DIR/native"
cp -a "$TEMP_DIR/$RUNTIME_NAME/." "$SCRIPT_DIR/runtime-spacemit/"
cp -a "$TEMP_DIR/$MODEL_NAME/." "$SCRIPT_DIR/model/"

echo "编译常驻唤醒服务…"
g++ -Wall -Wextra -Wpedantic -Werror -O2 -std=c++17 \
  "$SCRIPT_DIR/native/wakeword_daemon.cpp" \
  -L"$SCRIPT_DIR/runtime-spacemit/lib" \
  -lsherpa-onnx-c-api -lasound -pthread \
  -Wl,-rpath,'$ORIGIN/../runtime-spacemit/lib' \
  -o "$TEMP_DIR/wakeword-daemon"
cp "$TEMP_DIR/wakeword-daemon" "$SCRIPT_DIR/native/wakeword-daemon"
chmod 755 "$SCRIPT_DIR/native/wakeword-daemon"

LD_LIBRARY_PATH="$SCRIPT_DIR/runtime-spacemit/lib" \
  "$SCRIPT_DIR/runtime-spacemit/bin/sherpa-onnx-version"

echo "完成：$APP_DIR 已具备本地常驻语音唤醒资源"
