import io
import time
import pyautogui
import subprocess
import platform
import cv2
import logging
from flask import Flask, Response, request, jsonify, render_template_string, send_from_directory
from PIL import ImageGrab
import tkinter as tk
from threading import Thread
from queue import Queue
import psutil
import os

# 配置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
camera_status_queue = Queue()

# 创建一个目录用于存储上传的文件
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 初始设置
SCREEN_FRAME_RATE = 10
CAMERA_FRAME_RATE = 30
DEFAULT_SCREEN_QUALITY = 70  # 默认图像质量(0-100)
DEFAULT_CAMERA_QUALITY = 70
SCREEN_RESOLUTION_SCALE = 0.7  # 分辨率缩放因子
CAMERA_RESOLUTION_SCALE = 0.7
IS_MOBILE_MODE = False
IS_CLIENT_HIDDEN = False
root = None


class CameraProcessor:
    def __init__(self):
        self.camera = None
        try:
            self.camera = cv2.VideoCapture(0)
            self.camera.set(cv2.CAP_PROP_FPS, CAMERA_FRAME_RATE)
            status = "摄像头已打开" if self.camera.isOpened() else "摄像头未打开"
        except Exception as error:
            status = f"摄像头初始化出错: {error}"
            self.camera = None
        camera_status_queue.put(status)
        logger.info(status)

    def generate_camera_frames(self):
        if not self.camera:
            return
        try:
            while True:
                start_time = time.time()

                success, frame = self.camera.read()
                if success:
                    # 调整分辨率
                    if CAMERA_RESOLUTION_SCALE < 1.0:
                        new_size = (int(frame.shape[1] * CAMERA_RESOLUTION_SCALE),
                                    int(frame.shape[0] * CAMERA_RESOLUTION_SCALE))
                        frame = cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

                    # 设置JPEG压缩参数
                    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), DEFAULT_CAMERA_QUALITY]
                    result, buffer = cv2.imencode('.jpg', frame, encode_param)

                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

                else:
                    logger.warning("无法读取摄像头帧")
                    break

                # 精确控制帧率
                elapsed = time.time() - start_time
                sleep_time = max(0, (1.0 / CAMERA_FRAME_RATE) - elapsed)
                time.sleep(sleep_time)

        except Exception as error:
            logger.error(f"生成摄像头视频流出错: {error}")
        finally:
            if self.camera:
                self.camera.release()
                logger.info("摄像头已释放")


camera_processor = CameraProcessor()


def generate_screen_frames():
    try:
        while True:
            start_time = time.time()

            # 获取屏幕截图
            image = ImageGrab.grab()

            # 调整分辨率
            if SCREEN_RESOLUTION_SCALE < 1.0:
                new_size = (int(image.width * SCREEN_RESOLUTION_SCALE),
                            int(image.height * SCREEN_RESOLUTION_SCALE))
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            # 压缩图像
            image_byte_array = io.BytesIO()
            image.save(image_byte_array, format='JPEG', quality=DEFAULT_SCREEN_QUALITY, optimize=True)

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + image_byte_array.getvalue() + b'\r\n')

            # 精确控制帧率
            elapsed = time.time() - start_time
            sleep_time = max(0, (1.0 / SCREEN_FRAME_RATE) - elapsed)
            time.sleep(sleep_time)

    except Exception as error:
        logger.error(f"生成屏幕截图流出错: {error}")


@app.route('/video_stream')
def video_stream():
    return Response(generate_screen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/camera_stream')
def camera_stream():
    return Response(camera_processor.generate_camera_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/mouse_click', methods=['POST'])
def mouse_click():
    data = request.get_json()
    required_params = ['x', 'y', 'scale_x', 'scale_y', 'click_type']
    if any(param not in data for param in required_params):
        logger.error(f"鼠标点击请求缺少必要参数: {', '.join(param for param in required_params if param not in data)}")
        return jsonify(
            {"错误": f"缺少必要参数: {', '.join(param for param in required_params if param not in data)}"}), 400
    actual_x = int(data['x'] * data['scale_x'])
    actual_y = int(data['y'] * data['scale_y'])
    click_type = data['click_type']
    click_functions = {
        '左键': pyautogui.click,
        '右键': pyautogui.rightClick,
        '双击': pyautogui.doubleClick,
        '拖动': pyautogui.dragTo
    }
    click_function = click_functions.get(click_type)
    if click_type == '拖动':
        start_x = int(data.get('start_x', 0) * data['scale_x'])
        start_y = int(data.get('start_y', 0) * data['scale_y'])
        pyautogui.moveTo(start_x, start_y)
        pyautogui.mouseDown()
        click_function(actual_x, actual_y)
        pyautogui.mouseUp()
    elif click_function:
        click_function(actual_x, actual_y)
    else:
        logger.error(f"无效的点击类型: {click_type}")
        return jsonify({"错误": f"无效的点击类型: {click_type}"}), 400
    logger.info(f"鼠标 {click_type} 点击事件: 坐标 ({actual_x}, {actual_y})")
    return 'OK'


@app.route('/keyboard_press', methods=['POST'])
def keyboard_press():
    data = request.get_json()
    if 'key' not in data:
        logger.error("键盘输入请求缺少必要参数: 按键")
        return jsonify({"错误": "缺少必要参数: 按键"}), 400
    pyautogui.press(data['key'])
    logger.info(f"键盘输入事件: 按键 {data['key']}")
    return 'OK'


@app.route('/execute_command', methods=['POST'])
def execute_command():
    data = request.get_json()
    if 'command' not in data:
        logger.error("执行命令请求缺少必要参数: 命令")
        return jsonify({"错误": "缺少必要参数: 命令"}), 400
    result = subprocess.run(data['command'], shell=True, capture_output=True, text=True)
    output = result.stdout if result.stdout else result.stderr
    logger.info(f"执行命令: {data['command']}, 输出: {output}")
    return jsonify({'输出': output})


@app.route('/get_computer_info')
def get_computer_info():
    return jsonify({
        '操作系统': platform.system(),
        '版本号': platform.release(),
        '详细版本': platform.version(),
        '机器类型': platform.machine(),
        '处理器': platform.processor(),
        'CPU使用率': psutil.cpu_percent(interval=1),
        '内存使用率': psutil.virtual_memory().percent
    })


@app.route('/shutdown', methods=['POST'])
def shutdown():
    command = "shutdown /s /t 0" if platform.system() == "Windows" else "sudo shutdown -h now"
    try:
        subprocess.run(command, shell=True)
        logger.info("关机命令已发送")
        return jsonify({"消息": "关机命令已发送"})
    except Exception as error:
        logger.error(f"关机命令执行出错: {error}")
        return jsonify({"错误": str(error)}), 500


@app.route('/restart', methods=['POST'])
def restart():
    command = "shutdown /r /t 0" if platform.system() == "Windows" else "sudo shutdown -r now"
    try:
        subprocess.run(command, shell=True)
        logger.info("重启命令已发送")
        return jsonify({"消息": "重启命令已发送"})
    except Exception as error:
        logger.error(f"重启命令执行出错: {error}")
        return jsonify({"错误": str(error)}), 500


@app.route('/volume_control', methods=['POST'])
def volume_control():
    data = request.get_json()
    if 'action' not in data:
        logger.error("音量控制请求缺少必要参数: action")
        return jsonify({"错误": "缺少必要参数: action"}), 400
    action = data['action']
    if action == 'up':
        pyautogui.press('volumeup')
    elif action == 'down':
        pyautogui.press('volumedown')
    elif action == 'mute':
        pyautogui.press('volumemute')
    else:
        logger.error(f"无效的音量控制动作: {action}")
        return jsonify({"错误": f"无效的音量控制动作: {action}"}), 400
    logger.info(f"音量控制动作: {action}")
    return 'OK'


@app.route('/upload_file', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        logger.error("文件上传请求缺少文件")
        return jsonify({"错误": "缺少文件"}), 400
    file = request.files['file']
    if file.filename == '':
        logger.error("未选择文件")
        return jsonify({"错误": "未选择文件"}), 400
    if file:
        filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filename)
        logger.info(f"文件 {file.filename} 上传成功")
        return jsonify({"消息": "文件上传成功"})


@app.route('/set_frame_rate', methods=['POST'])
def set_frame_rate():
    global SCREEN_FRAME_RATE, CAMERA_FRAME_RATE
    data = request.get_json()
    if 'screen_frame_rate' in data:
        try:
            new_screen_frame_rate = int(data['screen_frame_rate'])
            if new_screen_frame_rate > 0:
                SCREEN_FRAME_RATE = new_screen_frame_rate
            else:
                return jsonify({"错误": "屏幕帧率必须为正整数"}), 400
        except ValueError:
            return jsonify({"错误": "屏幕帧率必须为正整数"}), 400
    if 'camera_frame_rate' in data:
        try:
            new_camera_frame_rate = int(data['camera_frame_rate'])
            if new_camera_frame_rate > 0:
                CAMERA_FRAME_RATE = new_camera_frame_rate
                if camera_processor.camera:
                    camera_processor.camera.set(cv2.CAP_PROP_FPS, new_camera_frame_rate)
            else:
                return jsonify({"错误": "摄像头帧率必须为正整数"}), 400
        except ValueError:
            return jsonify({"错误": "摄像头帧率必须为正整数"}), 400
    return jsonify({"消息": "帧率设置成功"})


@app.route('/set_mobile_mode', methods=['POST'])
def set_mobile_mode():
    global IS_MOBILE_MODE, SCREEN_FRAME_RATE, CAMERA_FRAME_RATE, SCREEN_RESOLUTION_SCALE, CAMERA_RESOLUTION_SCALE, DEFAULT_SCREEN_QUALITY, DEFAULT_CAMERA_QUALITY
    data = request.get_json()
    if 'is_mobile_mode' in data:
        IS_MOBILE_MODE = data['is_mobile_mode']
        if IS_MOBILE_MODE:
            # 手机模式下降低帧率和质量
            SCREEN_FRAME_RATE = 5
            CAMERA_FRAME_RATE = 15
            SCREEN_RESOLUTION_SCALE = 0.5
            CAMERA_RESOLUTION_SCALE = 0.5
            DEFAULT_SCREEN_QUALITY = 50
            DEFAULT_CAMERA_QUALITY = 50
            if camera_processor.camera:
                camera_processor.camera.set(cv2.CAP_PROP_FPS, CAMERA_FRAME_RATE)
        else:
            # 恢复默认设置
            SCREEN_FRAME_RATE = 10
            CAMERA_FRAME_RATE = 30
            SCREEN_RESOLUTION_SCALE = 0.7
            CAMERA_RESOLUTION_SCALE = 0.7
            DEFAULT_SCREEN_QUALITY = 70
            DEFAULT_CAMERA_QUALITY = 70
            if camera_processor.camera:
                camera_processor.camera.set(cv2.CAP_PROP_FPS, CAMERA_FRAME_RATE)
        return jsonify({"消息": f"手机模式已设置为 {IS_MOBILE_MODE}"})
    return jsonify({"错误": "缺少必要参数: is_mobile_mode"}), 400


@app.route('/set_client_hidden', methods=['POST'])
def set_client_hidden():
    global IS_CLIENT_HIDDEN, root
    data = request.get_json()
    if 'is_client_hidden' in data:
        IS_CLIENT_HIDDEN = data['is_client_hidden']
        if IS_CLIENT_HIDDEN:
            if root:
                root.withdraw()  # 隐藏窗口
        else:
            if root:
                root.deiconify()  # 显示窗口
        return jsonify({"消息": f"客户端图形界面已设置为 {'隐藏' if IS_CLIENT_HIDDEN else '显示'}"})
    return jsonify({"错误": "缺少必要参数: is_client_hidden"}), 400


@app.route('/set_stream_quality', methods=['POST'])
def set_stream_quality():
    global DEFAULT_SCREEN_QUALITY, DEFAULT_CAMERA_QUALITY, SCREEN_RESOLUTION_SCALE, CAMERA_RESOLUTION_SCALE
    data = request.get_json()

    if 'screen_quality' in data:
        quality = int(data['screen_quality'])
        if 0 <= quality <= 100:
            DEFAULT_SCREEN_QUALITY = quality
        else:
            return jsonify({"错误": "屏幕质量参数必须在0-100之间"}), 400

    if 'camera_quality' in data:
        quality = int(data['camera_quality'])
        if 0 <= quality <= 100:
            DEFAULT_CAMERA_QUALITY = quality
        else:
            return jsonify({"错误": "摄像头质量参数必须在0-100之间"}), 400

    if 'screen_resolution_scale' in data:
        scale = float(data['screen_resolution_scale'])
        if 0.1 <= scale <= 1.0:
            SCREEN_RESOLUTION_SCALE = scale
        else:
            return jsonify({"错误": "屏幕分辨率缩放因子必须在0.1-1.0之间"}), 400

    if 'camera_resolution_scale' in data:
        scale = float(data['camera_resolution_scale'])
        if 0.1 <= scale <= 1.0:
            CAMERA_RESOLUTION_SCALE = scale
        else:
            return jsonify({"错误": "摄像头分辨率缩放因子必须在0.1-1.0之间"}), 400

    return jsonify({"消息": "流质量设置已更新"})


def generate_html_template(title, content, return_button=True):
    mobile_css = """
        @media (max-width: 768px) {
            .btn {
                font-size: 1.2rem;
                padding: 10px 20px;
            }
            .form-control {
                font-size: 1.2rem;
                padding: 10px;
            }
            .btn-group-vertical a {
                display: block;
                width: 100%;
                margin-bottom: 10px;
            }
        }
    """ if IS_MOBILE_MODE else ""
    return_link = '<a href="/" class="btn btn-secondary back-button">返回菜单</a>' if return_button else ''
    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #f4f4f9;
            }}
            .btn {{
                transition: all 0.3s ease;
            }}
            .btn:hover {{
                transform: scale(1.05);
            }}
            .back-button {{
                position: absolute;
                top: 10px;
                left: 10px;
            }}
            .settings-button {{
                position: fixed;
                bottom: 20px;
                right: 20px;
            }}
            {mobile_css}
        </style>
    </head>
    <body>
        {return_link}
        <div class="container my-5">
            {content}
        </div>
        <button type="button" class="btn btn-primary settings-button" data-bs-toggle="modal" data-bs-target="#settingsModal">设置</button>
        <div class="modal fade" id="settingsModal" tabindex="-1" aria-labelledby="settingsModalLabel" aria-hidden="true">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="settingsModalLabel">设置</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <label for="screenFrameRate" class="form-label">屏幕截图帧率 (帧/秒)</label>
                            <input type="number" class="form-control" id="screenFrameRate" value="{SCREEN_FRAME_RATE}">
                        </div>
                        <div class="mb-3">
                            <label for="cameraFrameRate" class="form-label">摄像头帧率 (帧/秒)</label>
                            <input type="number" class="form-control" id="cameraFrameRate" value="{CAMERA_FRAME_RATE}">
                        </div>
                        <div class="mb-3">
                            <label for="screenQuality" class="form-label">屏幕图像质量 (0-100)</label>
                            <input type="number" class="form-control" id="screenQuality" min="0" max="100" value="{DEFAULT_SCREEN_QUALITY}">
                        </div>
                        <div class="mb-3">
                            <label for="cameraQuality" class="form-label">摄像头图像质量 (0-100)</label>
                            <input type="number" class="form-control" id="cameraQuality" min="0" max="100" value="{DEFAULT_CAMERA_QUALITY}">
                        </div>
                        <div class="mb-3">
                            <label for="screenResolutionScale" class="form-label">屏幕分辨率缩放 (0.1-1.0)</label>
                            <input type="number" step="0.1" class="form-control" id="screenResolutionScale" min="0.1" max="1.0" value="{SCREEN_RESOLUTION_SCALE}">
                        </div>
                        <div class="mb-3">
                            <label for="cameraResolutionScale" class="form-label">摄像头分辨率缩放 (0.1-1.0)</label>
                            <input type="number" step="0.1" class="form-control" id="cameraResolutionScale" min="0.1" max="1.0" value="{CAMERA_RESOLUTION_SCALE}">
                        </div>
                        <div class="mb-3 form-check">
                            <input type="checkbox" class="form-check-input" id="mobileModeCheckbox" {'checked' if IS_MOBILE_MODE else ''}>
                            <label class="form-check-label" for="mobileModeCheckbox">手机模式</label>
                        </div>
                        <div class="mb-3 form-check">
                            <input type="checkbox" class="form-check-input" id="clientHiddenCheckbox" {'checked' if IS_CLIENT_HIDDEN else ''}>
                            <label class="form-check-label" for="clientHiddenCheckbox">隐藏客户端图形界面</label>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                        <button type="button" class="btn btn-primary" onclick="saveSettings()">保存设置</button>
                    </div>
                </div>
            </div>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            function saveSettings() {{
                const screenFrameRate = document.getElementById('screenFrameRate').value;
                const cameraFrameRate = document.getElementById('cameraFrameRate').value;
                const screenQuality = document.getElementById('screenQuality').value;
                const cameraQuality = document.getElementById('cameraQuality').value;
                const screenResolutionScale = document.getElementById('screenResolutionScale').value;
                const cameraResolutionScale = document.getElementById('cameraResolutionScale').value;
                const mobileMode = document.getElementById('mobileModeCheckbox').checked;
                const clientHidden = document.getElementById('clientHiddenCheckbox').checked;

                // 设置帧率
                fetch('/set_frame_rate', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        screen_frame_rate: screenFrameRate, 
                        camera_frame_rate: cameraFrameRate
                    }})
                }});

                // 设置流质量
                fetch('/set_stream_quality', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        screen_quality: screenQuality,
                        camera_quality: cameraQuality,
                        screen_resolution_scale: screenResolutionScale,
                        camera_resolution_scale: cameraResolutionScale
                    }})
                }});

                // 设置手机模式
                fetch('/set_mobile_mode', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        is_mobile_mode: mobileMode
                    }})
                }});

                // 设置客户端隐藏
                fetch('/set_client_hidden', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{
                        is_client_hidden: clientHidden
                    }})
                }}).then(response => response.json())
                .then(data => {{
                    alert('所有设置已保存');
                    $('#settingsModal').modal('hide');
                }});
            }}
        </script>
    </body>
    </html>
    """


@app.route('/')
def home():
    width, height = pyautogui.size()
    return render_template_string(generate_html_template("桌面投影菜单", f"""
        <div class="text-center">
            <h1 class="display-4">磊牌远程控制</h1>
            <div class="btn-group-vertical mt-4">
                <a href="/remote_control" class="btn btn-primary">远程控制</a>
                <a href="/command_line" class="btn btn-primary">命令行</a>
                <a href="/computer_info" class="btn btn-primary">电脑参数</a>
                <a href="/camera_view" class="btn btn-primary">摄像头查看</a>
                <a href="/file_upload" class="btn btn-primary">文件上传</a>
                <button onclick="shutdownComputer()" class="btn btn-danger">远程关机</button>
                <button onclick="restartComputer()" class="btn btn-warning">远程重启</button>
                <div class="mt-3">
                    <button onclick="volumeUp()" class="btn btn-info">音量增大</button>
                    <button onclick="volumeDown()" class="btn btn-info">音量减小</button>
                    <button onclick="volumeMute()" class="btn btn-info">静音</button>
                </div>
            </div>
        </div>
        <script>
            function shutdownComputer() {{
                if (confirm('确定要关机吗？')) {{
                    fetch('/shutdown', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify({{}})
                    }})
                   .then(response => response.json())
                   .then(data => {{
                        alert(data.消息);
                    }})
                   .catch(error => {{
                        alert('错误: ' + error.message);
                    }});
                }}
            }}
            function restartComputer() {{
                if (confirm('确定要重启吗？')) {{
                    fetch('/restart', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify({{}})
                    }})
                   .then(response => response.json())
                   .then(data => {{
                        alert(data.消息);
                    }})
                   .catch(error => {{
                        alert('错误: ' + error.message);
                    }});
                }}
            }}
            function volumeUp() {{
                fetch('/volume_control', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{action: 'up'}})
                }});
            }}
            function volumeDown() {{
                fetch('/volume_control', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{action: 'down'}})
                }});
            }}
            function volumeMute() {{
                fetch('/volume_control', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{action: 'mute'}})
                }});
            }}
        </script>
    """, return_button=False))


@app.route('/remote_control')
def remote_control():
    width, height = pyautogui.size()
    touch_events = """
        let isDragging = false;
        let startX = 0;
        let startY = 0;

        video.addEventListener('touchstart', function(event) {
            event.preventDefault();
            if (event.touches.length === 1) {
                // 单点触摸模拟左键点击
                const touch = event.touches[0];
                const x = touch.offsetX || touch.layerX;
                const y = touch.offsetY || touch.layerY;
                const scaleX = screenWidth / video.offsetWidth;
                const scaleY = screenHeight / video.offsetHeight;
                fetch('/mouse_click', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({x: x, y: y, scale_x: scaleX, scale_y: scaleY, click_type: '左键'})
                });
            } else if (event.touches.length === 2) {
                // 双指触摸开始拖动
                isDragging = true;
                startX = event.touches[0].pageX;
                startY = event.touches[0].pageY;
            }
        });

        video.addEventListener('touchmove', function(event) {
            if (isDragging && event.touches.length === 2) {
                const touch = event.touches[0];
                const x = touch.offsetX || touch.layerX;
                const y = touch.offsetY || touch.layerY;
                const scaleX = screenWidth / video.offsetWidth;
                const scaleY = screenHeight / video.offsetHeight;
                fetch('/mouse_click', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({x: x, y: y, scale_x: scaleX, scale_y: scaleY, click_type: '拖动', start_x: startX, start_y: startY})
                });
            }
        });

        video.addEventListener('touchend', function(event) {
            isDragging = false;
        });
    """ if IS_MOBILE_MODE else ""
    return render_template_string(generate_html_template("远程控制", f"""
        <div class="text-center">
            <h2>远程控制</h2>
            <div id="video-container" class="mt-4">
                <img id="video" src="/video_stream" class="img-fluid">
            </div>
        </div>
        <script>
            const video = document.getElementById('video');
            const screenWidth = {width};
            const screenHeight = {height};
            let clickTimer = null;
            let isDragging = false;
            let dragStartX = 0;
            let dragStartY = 0;

            video.addEventListener('mousedown', function(event) {{
                isDragging = true;
                dragStartX = event.offsetX;
                dragStartY = event.offsetY;
            }});

            video.addEventListener('mousemove', function(event) {{
                if (isDragging) {{
                    const x = event.offsetX;
                    const y = event.offsetY;
                    const scaleX = screenWidth / video.offsetWidth;
                    const scaleY = screenHeight / video.offsetHeight;
                    fetch('/mouse_click', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify({{x: x, y: y, scale_x: scaleX, scale_y: scaleY, click_type: '拖动', start_x: dragStartX, start_y: dragStartY}})
                    }});
                }}
            }});

            video.addEventListener('mouseup', function(event) {{
                isDragging = false;
            }});

            video.addEventListener('click', function(event) {{
                if (clickTimer) {{
                    clearTimeout(clickTimer);
                    clickTimer = null;
                    sendClickRequest(event, '双击');
                }} else {{
                    clickTimer = setTimeout(() => {{
                        clickTimer = null;
                        sendClickRequest(event, '左键');
                    }}, 300);
                }}
            }});

            video.addEventListener('contextmenu', function(event) {{
                event.preventDefault();
                sendClickRequest(event, '右键');
            }});

            function sendClickRequest(event, clickType) {{
                const x = event.offsetX;
                const y = event.offsetY;
                const scaleX = screenWidth / video.offsetWidth;
                const scaleY = screenHeight / video.offsetHeight;
                fetch('/mouse_click', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{x: x, y: y, scale_x: scaleX, scale_y: scaleY, click_type: clickType}})
                }});
            }}

            document.addEventListener('keydown', function(event) {{
                const key = event.key;
                fetch('/keyboard_press', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json'
                    }},
                    body: JSON.stringify({{key: key}})
                }});
            }});

            {touch_events}
        </script>
    """))


@app.route('/command_line')
def command_line():
    return render_template_string(generate_html_template("命令行", f"""
        <div class="text-center">
            <h2>命令行</h2>
            <div id="terminal-container" class="mt-4">
                <div id="terminal-output" class="bg-dark text-white p-3" style="height: 300px; overflow-y: auto;"></div>
                <input type="text" id="terminal-input" class="form-control mt-3" placeholder="输入命令" {'inputmode="text"' if IS_MOBILE_MODE else ''}>
            </div>
        </div>
        <script>
            const terminalInput = document.getElementById('terminal-input');
            const terminalOutput = document.getElementById('terminal-output');
            terminalInput.addEventListener('keydown', function(event) {{
                if (event.key === 'Enter') {{
                    const command = terminalInput.value;
                    terminalInput.value = '';
                    fetch('/execute_command', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/json'
                        }},
                        body: JSON.stringify({{command: command}})
                    }})
                   .then(response => response.json())
                   .then(data => {{
                        const outputElement = document.createElement('pre');
                        outputElement.textContent = '$ ' + command + '\\n' + data.输出;
                        terminalOutput.appendChild(outputElement);
                        terminalOutput.scrollTop = terminalOutput.scrollHeight;
                    }})
                   .catch(error => {{
                        const errorElement = document.createElement('pre');
                        errorElement.textContent = '错误: ' + error.message;
                        terminalOutput.appendChild(errorElement);
                        terminalOutput.scrollTop = terminalOutput.scrollHeight;
                    }});
                }}
            }});
        </script>
    """))


@app.route('/computer_info')
def computer_info():
    return render_template_string(generate_html_template("电脑参数", f"""
        <div class="text-center">
            <h2>电脑参数</h2>
            <pre id="info-output" class="bg-dark text-white p-3 mt-4"></pre>
        </div>
        <script>
            fetch('/get_computer_info')
           .then(response => response.json())
           .then(data => {{
                const infoOutput = document.getElementById('info-output');
                let infoText = '';
                for (const key in data) {{
                    infoText += key + ': ' + data[key] + '\\n';
                }}
                infoOutput.textContent = infoText;
            }})
           .catch(error => {{
                const infoOutput = document.getElementById('info-output');
                infoOutput.textContent = '错误: ' + error.message;
            }});
        </script>
    """))


@app.route('/camera_view')
def camera_view():
    status = camera_status_queue.get() if not camera_status_queue.empty() else "未知状态"
    return render_template_string(generate_html_template("摄像头查看", f"""
        <div class="text-center">
            <h2>摄像头查看</h2>
            <p class="mt-4">摄像头状态: {status}</p>
            <img src="/camera_stream" class="img-fluid mt-3">
        </div>
    """))


@app.route('/file_upload')
def file_upload():
    return render_template_string(generate_html_template("文件上传", f"""
        <div class="text-center">
            <h2>文件上传</h2>
            <form id="upload-form" enctype="multipart/form-data">
                <input type="file" id="file-input" name="file" class="form-control mt-4">
                <button type="button" onclick="uploadFile()" class="btn btn-primary mt-3">上传文件</button>
            </form>
            <div id="upload-status" class="mt-3"></div>
        </div>
        <script>
            function uploadFile() {{
                const fileInput = document.getElementById('file-input');
                const file = fileInput.files[0];
                if (!file) {{
                    alert('请选择文件');
                    return;
                }}
                const formData = new FormData();
                formData.append('file', file);
                fetch('/upload_file', {{
                    method: 'POST',
                    body: formData
                }})
               .then(response => response.json())
               .then(data => {{
                    const statusDiv = document.getElementById('upload-status');
                    statusDiv.textContent = data.消息;
                }})
               .catch(error => {{
                    const statusDiv = document.getElementById('upload-status');
                    statusDiv.textContent = '错误: ' + error.message;
                }});
            }}
        </script>
    """))


def start_flask_server():
    app.run(debug=False, host='0.0.0.0', port=5000)


def start_gui():
    global root
    root = tk.Tk()
    root.title("远程桌面 by明磊")
    tk.Label(root, text="服务已启动，访问 http://localhost:5000 查看。").pack(pady=20)
    root.mainloop()


if __name__ == '__main__':
    Thread(target=start_flask_server, daemon=True).start()
    start_gui()
