# 修改离线唤醒词

小智使用内置的 sherpa-onnx 中文开放词汇 KWS 模型。修改中文唤醒词只需要更新关键词文件，不需要重新训练模型、编译 K230 程序，也不需要在设备上下载资源。

当前模型是 `sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01`，只支持中文。英文或中英混合唤醒词需要更换模型，不属于本文流程。关键词格式和 token 生成方式可参考 [sherpa-onnx 官方 KWS 文档](https://k2-fsa.github.io/sherpa/onnx/kws/index.html#keywords-file)。

## 需要修改的文件

| 文件 | 作用 |
| --- | --- |
| `wake/keywords.txt` | 真正参与本地识别的关键词、增益和阈值 |
| `wake/manifest.sha256` | 部署脚本使用的内置资源完整性清单 |
| 设备上的 `config.json` | `wake_word` 控制待机界面的提示文字，不负责识别 |
| `assistant.py`、`config.example.json` | 发布定制版本时使用的默认提示和配置示例 |

`deploy.sh` 会保留设备现有的 `config.json`，因此修改仓库里的关键词不会自动覆盖设备个性化配置。

## 示例：改成“你好小智”

以下命令均从仓库根目录开始执行。

### 1. 修改识别关键词

将 `app/xiaozhi/wake/keywords.txt` 改为一行：

```text
n ǐ h ǎo x iǎo zh ì :3.5 #0.10 @你好小智
```

这一行的结构是：

```text
拼音 token :增益 #触发阈值 @识别结果
```

- `n ǐ h ǎo x iǎo zh ì`：模型使用的 partial-pinyin token，token 之间必须有空格。
- `:3.5`：boosting score。数值越大越容易触发，也越容易误唤醒。
- `#0.10`：trigger threshold，范围为 0 到 1。数值越小越容易触发。
- `@你好小智`：识别成功后返回给 App 和服务端的文字。ppinyin 模型必须提供这一项，内容不能包含空格；需要分隔时使用下划线。

当一行包含 `:3.5` 和 `#0.10` 时，它们覆盖 `config.json` 中的全局 `wake_word_score` 和 `wake_word_threshold`。省略行内参数时才使用全局值。

### 2. 更新待机提示

如果只修改一台设备，在 `/data/app/xiaozhi/config.json` 中设置：

```json
{
  "wake_word": "你好小智"
}
```

配置加载时会自动补齐未写出的默认项。如果设备已经存在 `config.json`，只修改其中的 `wake_word`，不要删除已有的服务地址、令牌或音频设置。

如果要发布一个默认使用新唤醒词的版本，还应同步修改：

- `app/xiaozhi/assistant.py` 中 `DEFAULT_CONFIG` 的 `wake_word`
- `app/xiaozhi/config.example.json` 中的 `wake_word`
- 主 README 中展示给用户的唤醒词文字

### 3. 更新完整性清单

关键词文件属于内置资源。修改后运行下面的跨平台 Python 命令，自动更新 `manifest.sha256` 中对应的哈希：

```sh
cd app/xiaozhi
python3 - <<'PY'
from hashlib import sha256
from pathlib import Path

relative = "wake/keywords.txt"
manifest = Path("wake/manifest.sha256")
digest = sha256(Path(relative).read_bytes()).hexdigest()
lines = manifest.read_text(encoding="utf-8").splitlines()
lines = [
    f"{digest}  {relative}" if line.endswith(f"  {relative}") else line
    for line in lines
]
manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
```

然后验证唤醒资源和测试：

```sh
python3 -m unittest discover -s tests -p 'test_wakeword.py'
```

### 4. 自动部署

先从设备桌面退出正在运行的小智，再在仓库根目录执行：

```sh
./app/xiaozhi/deploy.sh 10.10.11.213
```

部署脚本会在复制前后分别校验内置资源，并保留设备原有的 `device.json` 和 `config.json`。部署完成后从桌面重新启动小智，新的关键词会在模型初始化时加载。

## 生成其他中文唤醒词

不建议凭感觉手写拼音 token，尤其要注意声调和 `zh`、`ch`、`sh` 等声母。推荐在开发电脑上使用 sherpa-onnx 官方工具生成；这一步不在 CyberCAM 设备上执行。

先创建原始关键词文件，例如 `/tmp/keywords_raw.txt`：

```text
你好小智 :3.5 #0.10 @你好小智
小智同学 :3.5 #0.10 @小智同学
```

如果开发环境尚未安装转换工具，可安装与内置运行库一致的版本：

```sh
python3 -m pip install 'sherpa-onnx==1.13.2'
```

然后生成关键词文件：

```sh
cd app/xiaozhi
sherpa-onnx-cli text2token \
  --tokens wake/model/tokens.txt \
  --tokens-type ppinyin \
  /tmp/keywords_raw.txt wake/keywords.txt
```

每行代表一个唤醒词，因此一个模型可以同时支持多个中文唤醒词。生成后仍需更新 `manifest.sha256`、部署并重启 App。

## 调整灵敏度

当前默认值为 `:3.5 #0.10`，在 sherpa-onnx 针对该 Wenetspeech KWS 模型的 C API 示例基础上进一步提高了关键词增益。若仍需调整，建议每次只改一个参数并在实际环境中重复测试。

| 现象 | 调整方法 |
| --- | --- |
| 经常叫不醒 | 小幅提高 boosting score，或小幅降低 trigger threshold |
| 经常误唤醒 | 小幅降低 boosting score，或小幅提高 trigger threshold |
| 只有某一个词表现不好 | 修改该行的 `:`、`#` 参数，不影响其他关键词 |

提高 score 和降低 threshold 都会让触发更容易，但也会增加误唤醒。官方定义中 threshold 必须位于 0 到 1 之间。

## 排查问题

- 部署时报 `wake/keywords.txt: FAILED`：关键词文件已经变化，但 `manifest.sha256` 尚未更新。
- 启动时报“内置唤醒资源缺失”：复制的 App 目录不完整，重新运行 `deploy.sh`。
- 一直停留在准备唤醒：检查是否存在不属于 `wake/model/tokens.txt` 的 token，建议重新运行 `text2token`。
- 识别正常但界面仍显示“小智小智”：设备 `config.json` 的 `wake_word` 尚未更新，或修改后没有重启 App。
- 修改后没有生效：关键词在模型初始化时读取，必须完全退出并重新启动小智。

真机日志出现下面的内容代表新关键词已被识别：

```text
[wake] DETECTED    你好小智
```
