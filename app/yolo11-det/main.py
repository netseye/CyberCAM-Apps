'''
实验名称：YOLO11检测
实验平台：cybercam
说明：摄像头采集检测
'''

from walnutpi import kpu, Display, Sensor, IDE
import cv2, time, colorsys


model_size = 224
model_path = "yolo11n.kmodel"
labels = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
    'train', 'truck', 'boat', 'traffic light', 'fire hydrant',
    'stop sign', 'parking meter', 'bench', 'bird', 'cat', 'dog',
    'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe',
    'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
    'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat',
    'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
    'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot',
    'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
    'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop',
    'mouse', 'remote', 'keyboard', 'cell phone', 'microwave',
    'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock',
    'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
]

# 字体配置（文泉驿正黑，字号 20）
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
FONT_SIZE = 30
ft2 = cv2.freetype.createFreeType2()
ft2.loadFontData(FONT_PATH, 0)

def _get_label_color(label_index, num_labels):
    r, g, b = colorsys.hsv_to_rgb(label_index / num_labels, 0.9, 0.8)
    return (int(b * 255), int(g * 255), int(r * 255))


# 加载模型 
yolo = kpu.YOLO11_DET(model_path, model_size)

Display.init()
cap = Sensor.Sensor(640, 480)
if not cap.isOpened():
    print("Cannot open camera")
    exit()

last_time = time.time()
fps = 0.0
while True:
    # 读取一帧
    ret, img = cap.read()
    if not ret:
        print("Can't receive frame (stream end?). Exiting ...")
        break

    # ========== 计算帧率：基于帧间时间差 ==========
    current_time = time.time()
    delta = current_time - last_time
    last_time = current_time
    
    if delta > 0:
        fps = round(1.0 / delta, 1)
    else:
        fps = 0.0  # 防止除零（理论上不会发生）

    # 阻塞式目标检测
    boxes = yolo.run(img, 0.5, 0.45)

    # 输出检测结果
    for box in boxes:
        print(
            "{:f} ({:4d},{:4d}) w{:4d} h{:4d} {:s}".format(
                box.reliability,
                box.x,
                box.y,
                box.w,
                box.h,
                labels[box.label],
            )
        )

    # 绘制检测框和中文标签 
    for box in boxes:
        color = _get_label_color(box.label, len(labels))
        label_text = f"{labels[box.label]} {box.reliability:.2f}"

        left_x = int(box.x - box.w / 2)
        left_y = int(box.y - box.h / 2)
        right_x = int(box.x + box.w / 2)
        right_y = int(box.y + box.h / 2)

        # 获取文字尺寸
        (label_width, label_height), baseline = ft2.getTextSize(label_text, FONT_SIZE, -1)

        # 防止标签超出图像顶部
        text_y = left_y - baseline
        bg_y1 = left_y - label_height - baseline
        if bg_y1 < 0:
            bg_y1 = 0
            text_y = label_height

        # 画检测框（不同标签不同颜色）
        cv2.rectangle(
            img,
            (left_x, left_y),
            (right_x, right_y),
            color,
            2,
        )

        # 绘制标签
        ft2.putText(
            img,
            label_text,
            (left_x, text_y),
            FONT_SIZE,
            color,
            -1,
            cv2.LINE_AA,
            True,
        )

    # 绘制 FPS
    fps_text = f"FPS: {fps}"
    (fps_w, fps_h), fps_base = ft2.getTextSize(fps_text, FONT_SIZE, -1)
    ft2.putText(
        img,
        fps_text,
        (10, 10 + fps_h),
        FONT_SIZE,
        (0, 0, 255),
        -1,
        cv2.LINE_AA,
        True,
    )

    # 显示图像
    Display.show(img)
    IDE.show(img)

# 清理
cap.release()
cv2.destroyAllWindows()