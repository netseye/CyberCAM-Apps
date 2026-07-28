# Apps for CyberCAM

**中文** | [English](./README_EN.md)

![img](./static/img/cybercam.png)

CyberCAM Linux系统支持自定义APP，用户可以通过按规范上传相关代码和配置文件，即可快速部署自己的APP并在系统GUI上显示。

APP目录位于CyberCAM系统文件系统的`/data/app`路径下。

![img](./static/img/1.png)

## APP定义规范

[完整教程>>](https://wiki.01studio.cc/docs/cybercam/os_software/custom_app)

以YOLO11检测APP举例。打开`yolo11-det`文件夹，文件如下：

![img](./static/img/2.png)

- app.txt : APP信息配置文件；
- run.sh : 执行APP时运行的脚本文件；
- main.py : APP的代码；
- yolo11n.kmodel : 代码调用的模型文件；
- icon_png : 用户上传的图标；
- icon_100.png : 系统自动生成的 100x100像素的图标文件。

## APP列表

### AI视觉（ai-vision）

机器视觉识别案例。

- 人脸相关：
    - 人脸检测(五关键点): [face-det](./app/ai-vision/face-det)
- 人体相关：
    - 人体检测: [person-det](./app/ai-vision/person-det)
    - 人体关键点: [person-keypoint](./app/ai-vision/person-keypoint)
    - 跌倒检测: [fall-det](./app/ai-vision/fall-det)
- 手部相关：
    - 手掌检测: [hand-det](./app/ai-vision/hand-det)
    - 手掌关键点检测: [hand-keypoint](./app/ai-vision/hand-keypoint)
    - 手掌关键点分类: [hand-keypoint-cls](./app/ai-vision/hand-keypoint-cls)
- 口罩识别: [mask-det](./app/ai-vision/mask-det)
- 车牌识别: [license-recg](./app/ai-vision/license-recg)
- 字符识别（OCR）: [ocr](./app/ai-vision/ocr)
- 吸烟检测: [smoke-det](./app/ai-vision/smoke-det)
- 交通灯识别: [traffic-light-recg](./app/ai-vision/traffic-light-recg)
- YOLO11检测: [yolo11-det](./app/ai-vision/yolo11-det)
- YOLO11分类: [yolo11-cls](./app/ai-vision/yolo11-cls)

### AI智能体（ai-agent）

语音对话、智能助手、大模型交互智能体。

- 小智AI: [xiaozhi](./app/ai-agent/xiaozhi/)

### 桌面小组件（desktop-widget）
天气时钟、日历、系统监控、待办事项等信息展示型应用。

### 智能家居/IoT（smart-home）
与智能家居联动，如设备控制面板、场景联动、家庭环境监测。

### 教育（education）
识字卡片、物体认知、手势教学等寓教于乐类应用。

### 工具（tool）
系统调试、传感器展示、实用小工具。

- IMU姿态仪: [imu-attitude](./app/tool/imu-attitude/)
- 蓝牙展示: [bt-showcase](./app/tool/bt-showcase/)

### 游戏（game）
体感游戏、AR 互动等。

### 工业质检（industrial）
缺陷检测、零件计数、流水线识别、安全帽/工服检测。

### 其它（other）

不清楚分类的先放这里。

## 贡献

欢迎贡献您的APP！请遵循以下步骤：
1. Fork本项目；
2. 在本地修改代码；
3. 提交Pull Request。   

请将将测试好的APP上传到`app`对应分类的目录下，并在您的APP文件夹内添加`README.md`文件，介绍你的APP和使用方法。

**对于优秀的APP，我们将会在CyberCAM系统中预装，并在官方文档中进行推荐。并给予开发者产品或其它形式奖励。**