import cv2
import time

from walnutpi import Display,Sensor,kpu,IDE
#【可选代码】允许Thonny远程运行
import os
os.environ["DISPLAY"] = ":0.0"

# 初始化屏幕
Display.init()

# 初始化人脸检测器，使用320x320模型
kmodel_path = "./face_detection_320.kmodel"
anchors_path = "./prior_data_320.bin"
detector = kpu.FACE_DETECT(kmodel_path, anchors_path,320)

# 初始化人脸检测器，使用640x640模型
# kmodel_path = "./face_detection_640.kmodel"
# anchors_path = "./prior_data_640.bin"
# detector = kpu.FACE_DETECT(kmodel_path, anchors_path,640)

# 初始化摄像头
cap = Sensor.Sensor(640, 480)
if not cap.isOpened():
    print("Cannot open camera")
    exit()

#计算帧率
last_time = time.time()
fps = 0.0
while True:
    
    #计算帧率
    current_time = time.time()
    delta = current_time - last_time
    last_time = current_time
    if delta > 0:
        fps = round(1.0 / delta, 1)
    else:
        fps = 0.0
    print("FPS: ", fps)
    
    # 摄像头读取一帧图像    
    ret, img = cap.read()
    if not ret:
        print("Can't receive frame (stream end?). Exiting ...")
        break
    
    # 执行目标检测，设置置信度阈值为 0.6，IoU 阈值为 0.7
    result = detector.run(img, reliability_threshold=0.6, nms_threshold=0.7)
        
    
    # 打印并绘制结果
    for result in result:
        # 绘制 人脸识别框
        cv2.rectangle(img, (result.x, result.y), 
                        (result.x + result.w, result.y + result.h), 
                        (0, 255, 0), 2)
        
        # 绘制置信度
        label = f"{result.reliability:.2f}"
        cv2.putText(img, label, (result.x, result.y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        # 绘制 5 个关键点
        cv2.circle(img, (result.left_eye.x, result.left_eye.y), 4, (0, 0, 255), -1) # 左眼
        cv2.circle(img, (result.right_eye.x, result.right_eye.y), 4, (0, 255, 255), -1) # 右眼
        cv2.circle(img, (result.nose.x, result.nose.y), 4, (255, 0, 255), -1) # 鼻子
        cv2.circle(img, (result.left_mouth.x, result.left_mouth.y), 4, (0, 255, 0), -1) # 左嘴角
        cv2.circle(img, (result.right_mouth.x, result.right_mouth.y), 4, (255, 0, 0), -1) # 右嘴角


    cv2.putText(img, 'FPS: '+str(fps), (10,30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2) #图像绘制帧率
    Display.show(img) #显示到屏幕上
    IDE.show(img) # 发送到ide窗口内显示
