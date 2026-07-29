# CyberCAM 应用商店

在 CyberCAM 设备上浏览、安装、更新和卸载应用。应用安装完成后，系统桌面会动态重新扫描 `/data/app` 并自动显示。

## 功能

- 从 01Studio 官方 GitHub 仓库下载单个应用目录，无需下载整个仓库；
- 获取 `main` 当前提交并固定到该提交下载，避免一次安装混入不同版本；
- 网络中断后按 HTTP Range 续传，并可在等价 HTTPS 地址间自动切换；
- 对每个文件执行 Git blob SHA 校验；
- 支持带 SHA-256 和 `manifest.json` 的第三方 HTTPS 压缩包；
- 在 `/data/.app-store/staging` 完整下载和校验后，再原子移动到 `/data/app/<app-id>`；
- 更新时保留清单声明的配置文件，失败自动恢复旧目录；
- 卸载操作移动到 `/data/.app-store/trash`，不会立即永久删除；
- 后台下载，触摸界面持续显示下载进度。

## 操作

- 点应用卡片：选择应用；
- 点底部中间按钮：安装、更新或卸载；
- 卸载和覆盖已有非商店应用需要在 3 秒内再次确认；
- 点右上角“刷新”：重新获取在线目录；
- 点左上角 `×` 或长按实体 KEY 2 秒：退出；
- 短按实体 KEY：执行当前选中应用的主要操作。

安装或更新过程中不能退出，避免中断操作。即使设备意外断电，未完成内容也只会留在隐藏暂存目录，不会以应用形式被桌面扫描。

## 官方源

内置目录文件为 `catalog.json`，在线地址为：

```text
https://raw.githubusercontent.com/01studio-lab/CyberCAM-Apps/main/app/tool/app-store/catalog.json
```

官方应用使用 `github_tree` 源。商店优先从 `raw.githubusercontent.com` 下载目标应用目录内的文件；直连出现瞬态网络错误时，会尝试目录声明的 HTTPS 镜像并从已完成字节继续。无论实际下载地址是什么，每个文件都必须通过官方目录中的 Git blob SHA 校验，校验失败时不会安装。

官方目录已经内嵌每个文件的 Git SHA、大小和应用目录 tree SHA，因此正常刷新和安装不消耗 GitHub REST API 限额。只有没有内嵌清单的自定义 GitHub 源才使用 API。官方应用目录变化后，在仓库根目录运行：

```sh
python app/tool/app-store/build_catalog.py
```

然后一并提交更新后的 `catalog.json`。

## 添加其他应用源

首次需要自定义时，将 `sources.json` 复制到：

```text
/data/.app-store/sources.json
```

然后增加一个目录地址：

```json
{
  "schema_version": 1,
  "catalogs": [
    {
      "name": "01Studio 官方",
      "url": "https://raw.githubusercontent.com/01studio-lab/CyberCAM-Apps/main/app/tool/app-store/catalog.json",
      "trusted": true,
      "enabled": true,
      "fallback": "catalog.json"
    },
    {
      "name": "我的应用源",
      "url": "https://example.com/cybercam/catalog.json",
      "trusted": false,
      "enabled": true
    }
  ]
}
```

仅支持 HTTPS，地址中不能包含用户名、密码或 URL 片段。

## 第三方压缩包格式

第三方目录可以声明 `archive` 源：

```json
{
  "schema_version": 1,
  "sources": {
    "downloads": {
      "kind": "archive"
    }
  },
  "apps": [
    {
      "id": "hello-camera",
      "name_cn": "相机示例",
      "name_en": "Hello Camera",
      "category": "other",
      "version": "1.0.0",
      "source": "downloads",
      "package_url": "https://example.com/hello-camera-1.0.0.tar.gz",
      "size": 123456,
      "sha256": "填写压缩包的64位小写SHA-256"
    }
  ]
}
```

压缩包根目录或唯一一级子目录必须包含：

```text
app.txt
run.sh
icon.png
manifest.json
```

`manifest.json` 最少包含：

```json
{
  "schema_version": 1,
  "id": "hello-camera",
  "version": "1.0.0",
  "persistent_files": [
    "config.json"
  ]
}
```

商店拒绝绝对路径、`..`、反斜杠路径、符号链接、硬链接、设备文件、加密 ZIP、重复路径和超限解压内容，也不会执行压缩包中的安装脚本。

第三方源会在界面中标记，并要求二次确认。目录维护者仍应通过受保护的 HTTPS 站点发布目录及其 SHA-256；后续版本可在此基础上增加离线公钥签名。

## 数据目录

```text
/data/app/<app-id>/             已安装应用
/data/.app-store/sources.json   用户应用源配置
/data/.app-store/staging/       下载和解压暂存
/data/.app-store/backups/       更新过程临时备份
/data/.app-store/trash/         可恢复的卸载目录
```

## 本地测试

核心模块不依赖 `cv2` 或 `walnutpi`：

```sh
python -m unittest discover -s app/tool/app-store/tests -v
```
