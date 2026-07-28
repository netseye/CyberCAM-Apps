'''
实验名称：车牌识别
实验平台：CyberCAM
'''

import cv2, time, os, colorsys
from walnutpi import kpu, Display, Sensor, IDE, direction


# 优先当前文件夹下相对路径（app离线部署）
if os.path.exists("./licence_det.kmodel"):
    det_model_path = "./licence_det.kmodel"
# 使用系统绝对路径（IDE运行调试）
elif os.path.exists("/data/app/license-recg/licence_det.kmodel"):
    det_model_path = "/data/app/license-recg/licence_det.kmodel"
else:
    raise FileNotFoundError("licence_det.kmodel 模型文件缺失，请检查当前路径与系统路径下的模型文件是否存在。")

# 优先当前文件夹下相对路径（app离线部署）
if os.path.exists("./licence_rec.kmodel"):
    rec_model_path = "./licence_rec.kmodel"
# 使用系统绝对路径（IDE运行调试）
elif os.path.exists("/data/app/license-recg/licence_rec.kmodel"):
    rec_model_path = "/data/app/license-recg/licence_rec.kmodel"
else:
    raise FileNotFoundError("licence_rec.kmodel 模型文件缺失，请检查当前路径与系统路径下的模型文件是否存在。")

# 优先当前文件夹下相对路径（app离线部署）
if os.path.exists("./anchors_640.bin"):
    anchors_bin_path = "./anchors_640.bin"
# 使用系统绝对路径（IDE运行调试）
elif os.path.exists("/data/app/license-recg/anchors_640.bin"):
    anchors_bin_path = "/data/app/license-recg/anchors_640.bin"
else:
    raise FileNotFoundError("anchors_640.bin 文件缺失，请检查当前路径与系统路径下的模型文件是否存在。")

# 车牌字符字典（74 类）
reco_labels = [
    "挂","使","领","澳","港","皖","沪","津","渝","冀","晋","蒙","辽","吉","黑","苏",
    "浙","京","闽","赣","鲁","豫","鄂","湘","粤","桂","琼","川","贵","云","藏","陕",
    "甘","青","宁","新","警","学",
    "0","1","2","3","4","5","6","7","8","9",
    "A","B","C","D","E","F","G","H","J","K","L","M","N","P","Q","R","S","T","U","V","W","X","Y","Z",
    "_","-",
]
det_size = 640 #检测模型尺寸
rec_size = (220, 32) #ocr模型尺寸
detector = kpu.LICENCE_DETECT(det_model_path, rec_model_path, anchors_bin_path,
                   reco_labels, det_size, rec_size) # 加载模型 

#字符显示改进，支持中英文显示
ft = cv2.freetype.createFreeType2() #创建freetype渲染器
ft.loadFontData("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0) #加载字体文件, 文泉驿正黑

def putText_Chinese(img, text, org, fontScale=30, color=(0, 255, 0)):
    
    global ft  # 使用全局的 FreeType 渲染器实例
    
    # 绘制中文
    ft.putText(
        img=img,
        text=text,
        org=org,
        fontHeight=fontScale,
        color=color,
        thickness=-1,        # 笔画粗细
        line_type=cv2.LINE_AA,  # 抗锯齿，文字更平滑
        bottomLeftOrigin=True  # False:坐标为左上角; True:与原生cv2.putText一致（左下角）
    )
    return img

# 初始化屏幕
Display.init()

# 初始化摄像头
cap = Sensor.Sensor(640, 480)
if not cap.isOpened():
    print("Cannot open camera")
    exit()

#获取当前显示屏方向，0表示默认，2表示180度翻转。
lcd_dir=direction.get_lcd() 
#print(lcd_dir) 

# 判断显示屏是否翻转，如果翻转，则设置显示旋转180°，摄像头同时设置为前置模式（水平镜像）
if lcd_dir == 2: #翻转了
    Display.set_rotation(2)
    cap.set_hmirror(1)

# ========== FPS计算 ==========
frame_count = 0       # 帧数计数器
start_time = time.time()
fps = 0.0

while True:
    
    # 摄像头读取一帧
    ret, img = cap.read()

    # 阻塞式目标检测
    results = detector.run(img)

    FONT_SIZE = 30 # 字体大小
    color =  (0, 0, 255)

    # 输出检测结果
    for r in results:
        print(f"{r.reliability:.2f}: {r.text}  {r.corners}")

        # 绘制边框
        pts = r.corners
        cv2.line(img, pts[0], pts[1], (0, 0, 255), 2)
        cv2.line(img, pts[1], pts[2], (0, 0, 255), 2)
        cv2.line(img, pts[2], pts[3], (0, 0, 255), 2)
        cv2.line(img, pts[3], pts[0], (0, 0, 255), 2)

        # 绘制车牌信息
        putText_Chinese(img, r.text, (pts[0][0], max(pts[0][1]- 8, 24)), fontScale=FONT_SIZE, color=color)
 
    # 每满1秒计算一次平均FPS
    frame_count += 1    
    current_time = time.time()
    if current_time - start_time >= 1.0:
        fps = frame_count / (current_time - start_time)
        frame_count = 0              # 重置帧数计数器
        start_time = current_time    # 重置计时起点
        print("FPS: ", f'FPS: {fps:.1f}')

    #FPS显示
    putText_Chinese(img, f'FPS: {fps:.1f}',  (10, 30), fontScale=30, color=(0, 255, 0))

    # 显示图像
    Display.show(img)
    IDE.show(img)