import cv2, time, os, colorsys
from walnutpi import kpu, Display, Sensor, IDE, direction

# 优先当前文件夹下相对路径（app离线部署）
if os.path.exists("./hand_det.kmodel") and os.path.exists("./handkp_det.kmodel"):
    hand_det_path = "./hand_det.kmodel"
    hand_kp_path = "./handkp_det.kmodel"

# 使用系统绝对路径（IDE运行调试）
elif os.path.exists("/data/app/hand-keypoint-cls/hand_det.kmodel") and \
     os.path.exists("/data/app/hand-keypoint-cls/handkp_det.kmodel"):
    hand_det_path = "/data/app/hand-keypoint-cls/hand_det.kmodel"
    hand_kp_path = "/data/app/hand-keypoint-cls/handkp_det.kmodel"

else:
    raise FileNotFoundError("模型文件缺失，请检查当前路径与系统路径下的模型文件是否存在。")

detector = kpu.HAND_KEYPOINT_CLS(hand_det_kmodel=hand_det_path,
                                  hand_kp_kmodel=hand_kp_path)

labels = ["fist", "five", "gun", "love", "one", "six", "three", "thumbUp", "yeah"]

# 手势颜色映射（基于编号）
GESTURE_COLORS = [
    (0, 0, 255),    # 0: fist
    (0, 255, 0),    # 1: five
    (255, 0, 0),    # 2: gun
    (255, 0, 255),  # 3: love
    (255, 255, 0),  # 4: one
    (0, 255, 255),  # 5: six
    (128, 128, 0),  # 6: three
    (128, 0, 128),  # 7: thumbUp
    (0, 128, 128),  # 8: yeah
]

#字符显示改进，支持中英文显示
ft = cv2.freetype.createFreeType2()
ft.loadFontData("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0)

def putText_Chinese(img, text, org, fontScale=30, color=(0, 255, 0)):
    global ft
    ft.putText(
        img=img,
        text=text,
        org=org,
        fontHeight=fontScale,
        color=color,
        thickness=-1,
        line_type=cv2.LINE_AA,
        bottomLeftOrigin=True
    )
    return img

def _get_label_color(label_index, num_labels):
    r, g, b = colorsys.hsv_to_rgb(label_index / num_labels, 0.9, 0.8)
    return (int(b * 255), int(g * 255), int(r * 255))


# 初始化屏幕
Display.init()

# 初始化摄像头
cap = Sensor.Sensor(640, 480)
if not cap.isOpened():
    print("Cannot open camera")
    exit()

#获取当前显示屏方向，0表示默认，2表示180度翻转。
lcd_dir = direction.get_lcd()

# 判断显示屏是否翻转，如果翻转，则设置显示旋转180°，摄像头同时设置为前置模式（水平镜像）
if lcd_dir == 2:
    Display.set_rotation(2)
    cap.set_hmirror(1)

# ========== FPS计算 ==========
frame_count = 0
start_time = time.time()
fps = 0.0

# 手部骨架连接
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),       # 拇指
    (0, 5), (5, 6), (6, 7), (7, 8),       # 食指
    (0, 9), (9, 10), (10, 11), (11, 12),  # 中指
    (0, 13), (13, 14), (14, 15), (15, 16),# 无名指
    (0, 17), (17, 18), (18, 19), (19, 20),# 小指
]

FINGER_COLORS = [
    (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 255, 255), (255, 0, 255),
]

while True:

    # 摄像头读取一帧
    ret, img = cap.read()

    # 手势识别
    results = detector.run(img, 0.2, 0.5)

    # 绘制结果
    for result in results:
        print(f"rel={result.reliability:.3f} "
              f"label={labels[result.label]} "
              f"box=({result.x},{result.y},{result.w},{result.h})")
        
        name = labels[result.label]
        color = _get_label_color(result.label, len(labels))

        left_x = int(result.x)
        left_y = int(result.y)
        right_x = int(result.x + result.w)
        right_y = int(result.y + result.h)

        # 手势标签
        label_text = f"{name} {result.reliability:.2f}"

        # 获取文字尺寸
        (label_width, label_height), baseline = ft.getTextSize(label_text, 30, -1)

        # 防止标签超出图像顶部
        text_y = left_y - baseline
        bg_y1 = left_y - label_height - baseline
        if bg_y1 < 0:
            bg_y1 = 0
            text_y = label_height

        # 画检测框
        cv2.rectangle(img, (left_x, left_y), (right_x, right_y), color, 2)
        putText_Chinese(img, label_text, (left_x, text_y), fontScale=30, color=color)

        # 绘制关键点
        kps = result.keypoints
        for i, kp in enumerate(kps):
            cv2.circle(img, (kp.x, kp.y), 3, (0, 255, 0), -1)

        # 绘制手指连线
        if len(kps) >= 21:
            for idx, (start, end) in enumerate(HAND_CONNECTIONS):
                finger_idx = idx // 4
                line_color = FINGER_COLORS[finger_idx] if finger_idx < 5 else (128, 128, 128)
                cv2.line(img,
                         (kps[start].x, kps[start].y),
                         (kps[end].x, kps[end].y),
                         line_color, 2)

    # 每满1秒计算一次平均FPS
    frame_count += 1
    current_time = time.time()
    if current_time - start_time >= 1.0:
        fps = frame_count / (current_time - start_time)
        frame_count = 0
        start_time = current_time
        print(f"FPS: {fps:.1f}")

    #FPS显示
    putText_Chinese(img, f'FPS: {fps:.1f}',  (10, 30), fontScale=30, color=(0, 255, 0))

    # 显示图像
    Display.show(img)
    IDE.show(img)
