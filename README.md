# Apps for CyberCAM

![img](./static/img/cybercam.png)

CyberCAM Linux系统支持自定义APP，用户可以通过按规范上传相关代码和配置文件，即可快速部署自己的APP并在系统GUI上显示。

APP目录位于CyberCAM系统文件系统的`/data/app`路径下。

![img](./static/img/1.png)

## APP定义规范

[完整教程>>](https://wiki.01studio.cc/docs/cybercam/intro/os_software/custom_app)

以YOLO11检测APP举例。打开`yolo11-det`文件夹，文件如下：

![img](./static/img/2.png)

- app.txt : APP信息配置文件；
- run.sh : 执行APP时运行的脚本文件；
- main.py : APP的代码；
- yolo11n.kmodel : 代码调用的模型文件；
- icon_png : 用户上传的图标；
- icon_100.png : 系统自动生成的 100x100像素的图标文件。

## APP列表

- [face-det](./app/face-det/README.md) : 人脸检测(支持五点关键点检测)；
- yolo11-det : YOLO11检测案例；
- yolo11-cls : YOLO11分类案例；

## 贡献

欢迎贡献您的APP！请遵循以下步骤：
1. Fork本项目；
2. 在本地修改代码；
3. 提交Pull Request。   

请将将测试好的APP上传到`app`目录下，并在您的APP文件夹内添加`README.md`文件，介绍你的APP和使用方法。

**对于优秀的APP，我们将会在CyberCAM系统中预装，并在官方文档中进行推荐。并给予开发者产品或其它形式奖励。**