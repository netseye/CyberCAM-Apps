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

APP_PARENT=/data/app
APP_DIR=$APP_PARENT/xiaozhi
STAGE_DIR=
BACKUP_DIR=$APP_PARENT/.xiaozhi-backup.$$

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ -n "$STAGE_DIR" ] && [ -d "$STAGE_DIR" ]; then
        rm -rf "$STAGE_DIR"
    fi
    if [ ! -d "$APP_DIR" ] && [ -d "$BACKUP_DIR" ]; then
        mv "$BACKUP_DIR" "$APP_DIR" || true
    fi
    exit "$status"
}

trap cleanup EXIT HUP INT TERM

if ps -eo args | grep -q "[p]ython -u main.py"; then
    echo "错误：小智正在运行，请先退出 App 后再部署" >&2
    exit 1
fi

mkdir -p "$APP_PARENT"
if [ -e "$BACKUP_DIR" ]; then
    echo "错误：部署备份目录已存在：$BACKUP_DIR" >&2
    exit 1
fi
STAGE_DIR=$(mktemp -d "$APP_PARENT/.xiaozhi-stage.XXXXXX")

tar -xzf - -C "$STAGE_DIR"
cd "$STAGE_DIR"
sha256sum -c wake/manifest.sha256

for persistent_file in config.json device.json; do
    if [ -f "$APP_DIR/$persistent_file" ]; then
        cp -p "$APP_DIR/$persistent_file" "$STAGE_DIR/$persistent_file"
    fi
done

rm -f "$STAGE_DIR/wake/install.sh"
chmod 755 \
    "$STAGE_DIR/run.sh" \
    "$STAGE_DIR/deploy.sh" \
    "$STAGE_DIR/wake/native/wakeword-daemon" \
    "$STAGE_DIR/wake/runtime-spacemit/bin/sherpa-onnx-keyword-spotter-alsa"

if [ -d "$APP_DIR" ]; then
    mv "$APP_DIR" "$BACKUP_DIR"
fi
if ! mv "$STAGE_DIR" "$APP_DIR"; then
    if [ -d "$BACKUP_DIR" ]; then
        mv "$BACKUP_DIR" "$APP_DIR"
    fi
    exit 1
fi
STAGE_DIR=
rm -rf "$BACKUP_DIR"
echo "部署完成：可直接从桌面启动小智"
'
