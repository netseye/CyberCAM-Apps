#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEVICE=${1:-10.10.11.213}

case "$DEVICE" in
    -*|*[!A-Za-z0-9._@:-]*)
        echo "错误：无效的设备地址：$DEVICE" >&2
        exit 2
        ;;
esac

case "$DEVICE" in
    *@*) SSH_TARGET=$DEVICE ;;
    *) SSH_TARGET=root@$DEVICE ;;
esac

echo "正在部署小智到 $SSH_TARGET:/data/app/xiaozhi"

# macOS 的 bsdtar 默认附带目标机不认识的扩展属性；关闭后只传 App 文件。
export COPYFILE_DISABLE=1

tar \
    --no-xattrs \
    --exclude='./config.json' \
    --exclude='./device.json' \
    --exclude='./__pycache__' \
    --exclude='./tests/__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.log' \
    -C "$APP_DIR" -czf - . | ssh "$SSH_TARGET" '
set -eu

APP_DIR=/data/app/xiaozhi

if ps -eo args | grep -q "[p]ython -u main.py"; then
    echo "错误：小智正在运行，请先退出 App 后再部署" >&2
    exit 1
fi

TEMP_DIR=$(mktemp -d /tmp/xiaozhi-deploy.XXXXXX)
trap '\''rm -rf "$TEMP_DIR"'\'' EXIT HUP INT TERM

tar -xzf - -C "$TEMP_DIR"
cd "$TEMP_DIR"
sha256sum -c wake/manifest.sha256

mkdir -p "$APP_DIR"
cp -a "$TEMP_DIR/." "$APP_DIR/"
rm -f "$APP_DIR/wake/install.sh"
chmod 755 \
    "$APP_DIR/run.sh" \
    "$APP_DIR/deploy.sh" \
    "$APP_DIR/wake/native/wakeword-daemon" \
    "$APP_DIR/wake/runtime-spacemit/bin/sherpa-onnx-keyword-spotter-alsa"

cd "$APP_DIR"
sha256sum -c wake/manifest.sha256
echo "部署完成：可直接从桌面启动小智"
'
