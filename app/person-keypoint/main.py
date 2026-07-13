import cv2, time, os, colorsys
from walnutpi import kpu, Display, Sensor, IDE, direction

# 优先当前文件夹下相对路径（app离线部署）
if os.path.exists("./yolov8n-pose.kmodel"):
    model_path = "./yolov8n-pose.kmodel"

# 使用系统绝对路径（IDE运行调试）
elif os.path.exists("/data/app/person-keypoint/yolov8n-pose.kmodel"):
    model_path = "/data/app/person-keypoint/yolov8n-pose.kmodel"

else:
    raise FileNotFoundError("模型文件缺失，请检查当前路径与系统路径下的模型文件是否存在。")

model_size = 320 #模型尺寸
detector = kpu.PERSON_KEYPOINT(model_path, model_size) # 加载模型 

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
    boxes = detector.run(img, 0.5, 0.45)

    # 输出检测结果
    '''
    for box in boxes:
        print(
            "{:f} ({:4d},{:4d}) w{:4d} h{:4d}".format(
                box.reliability,
                box.x,
                box.y,
                box.w,
                box.h,
            )
        )
    '''

    # 绘制检测框和中文标签 
    for box in boxes:

        FONT_SIZE = 30 # 字体大小

        color = (0, 255, 0)
        label_text = f"person {box.reliability:.2f}"

        left_x = int(box.x)
        left_y = int(box.y)
        right_x = int(box.x + box.w)
        right_y = int(box.y + box.h)

        # 获取文字尺寸
        (label_width, label_height), baseline = ft.getTextSize(label_text, FONT_SIZE, -1)

        # 防止标签超出图像顶部
        text_y = left_y - baseline
        bg_y1 = left_y - label_height - baseline
        if bg_y1 < 0:
            bg_y1 = 0
            text_y = label_height

        # 画检测框
        cv2.rectangle(img, (left_x, left_y), (right_x, right_y), color, 2)
        #输出字符
        putText_Chinese(img, label_text, (box.x, box.y), fontScale=FONT_SIZE, color=color)

        # 绘制骨架连线（带颜色）
        SKELETON = [
            (15, 13), (13, 11), (16, 14), (14, 12), (11, 12),
            (5, 11),  (6, 12),  (5, 6),
            (5, 7),   (6, 8),   (7, 9),   (8, 10),
            (1, 2),   (0, 1),   (0, 2),   (1, 3),   (2, 4),
            (3, 5),   (4, 6),
        ]
        for limb_idx, (a, b) in enumerate(SKELETON):
            kp_a = box.keypoints[a]
            kp_b = box.keypoints[b]
            if kp_a.confidence > 0.5 and kp_b.confidence > 0.5:
                color = detector.LIMB_COLORS[limb_idx]
                cv2.line(img, (kp_a.x, kp_a.y), (kp_b.x, kp_b.y),
                        color, 2)

        # 绘制 17 个关键点（不同颜色）
        for k, kp in enumerate(box.keypoints):
            if kp.confidence > 0.5:
                color = detector.KPS_COLORS[k]
                cv2.circle(img, (kp.x, kp.y), 4, color, -1)
                
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