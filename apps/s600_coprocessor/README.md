# S600 外挂多模态算法演示

地瓜机器人公司领导牵头的萝博头正式项目；维护者作为实习生负责相关算法。团队已验证 X5 官方行走、舞蹈链路；本应用不重做底层动作，不连接 X5/CAN，不发送运动指令。

## 已运行的能力

| 路径 | 实测结果 | 范围 |
| --- | --- | --- |
| Astra RGB → YOLO11x-Seg / S600 BPU | 30秒真实采集约19.26FPS | 原生深度同时采集；不是配准目标测距 |
| 四实例分核 YOLO11x-Seg | 合计217.64次/秒，单实例55.52次/秒 | 固定公开图片20秒回放，约3.92倍，不是四台相机 |
| RGB → Qwen3-VL-2B / S600 BPU | 原生视觉推理31.192ms、解码96.41tokens/s | 一次场景测试，冷加载墙钟9.18秒；网页另一次约2.38秒 |
| Whisper-medium / S600 BPU | 四条通用中文TTS，原生RTF 0.086–0.185 | 三条简体逐字相同，一条繁体语义相同；不是现场准确率 |
| M260C → Whisper → Qwen / S600 BPU | 真实5秒内存录音完成转写和图片问答 | 无已知现场原句，不判定转写正确；不执行口令动作 |
| RTX5090 FP32 → 官方十输出ONNX | 十个张量数值检查全部通过；CUDA前向P50 21.18ms | 模型开发/导出验证，无新增训练；未证明现成HBM与此PT同源 |
| STL-19P → 原生雷达局部规划 | 真实串口采集、CRC校验、连续规划运行 | 现场近场遮挡使起点受阻，不清障点制造可通行结果 |

这是可观察的边缘算法原型，不是自主导航交付。不同模型的时间、采样数量和计时范围不能直接组成平台排名；没有声称560TOPS满载。Qwen/Whisper共享四核资源，语音和视觉语言请求串行；后台视觉并行，未证明任意多模型并发均满足实时期限。

## 源码入口

- `product_demo.py` / `dashboard.html`：loopback网页、实时分割、雷达/原生深度状态、场景问答、5秒语音入口；每轮采集有界，结束后可点击重新采集。
- `vision.py`、`rgbd_bridge.cpp`：官方YOLO包装器、UVC彩色及独立OpenNI深度；内参无效时距离保持null。
- `sensor.py` / `acquisition/`：雷达只读采集、M260C处理后单声道、Vosk辅助文本请求；不等于阵列多通道/声源定位。
- `vlm_once.py` / `whisper_once.py`：官方OELLM原生demo的有界子进程封装，90秒超时，仅进程级运行库路径。
- `scene.py`：MIT python-pathfinding A*，未知区域阻塞、假设0.30m圆形足迹、雷达原生坐标系前方2m研究目标。
- `suite.py` / `sensor_bundle.py`：独立单核/同核/分核/四核回放和传感器压力测试。
- `gpu_reference.py`：RTX CUDA FP32、官方十输出导出及ONNX数值校验。
- `fetch_foundation_models.py`：五个官方模型文件大小与SHA256固定校验；模型不随仓库再分发。

## 部署前提

本次实板为S600 MCB V0p2、RDK OS5.1.0，系统Python的hbm_runtime0.2.0/UCP3.13.6。登录默认Python可能没有hbm_runtime，必须显式使用 `/usr/bin/python3`。现代模型仅子进程加载官方OELLM1.0.5 SDK运行库/UCP3.14.6，不覆盖系统。新板须核对兼容性，不能复制其他型号库。

需要NumPy/OpenCV、g++、OpenNI2头文件、Orbbec Astra ARM64 OpenNI2运行库、Vosk模型、pathfinding。从Vosk官方模型页下载通用中文small-cn-0.22并解压至 `models/vosk-model-small-cn-0.22`。麦克风和雷达必须空闲，声卡/USB节点按实板确认；默认CP2102的by-id路径不保证其他设备相同。深度子进程使用既有 `sudo -n` 权限，权限不足先由团队按平台规范配置，应用不修改udev、网络或服务。

所有操作在自己的开发目录内，保留 `ROS_DOMAIN_ID=42`。输出目录每次取新名字。先准备官方依赖：

```bash
export ROS_DOMAIN_ID=42 PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
git clone --filter=blob:none --sparse --branch rdk_s https://github.com/D-Robotics/rdk_model_zoo.git upstream
git -C upstream checkout 380e1a2bf42041af54be6f34935e50197cfadff9
git -C upstream sparse-checkout set samples/vision/ultralytics_yolo utils datasets
(cd upstream/samples/vision/ultralytics_yolo/model && bash download_model.sh s600 yolo11 seg x)
# 将下载的YOLO11x-Seg HBM放在本应用目录，保持下方固定文件名。
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install vosk==0.3.45
.venv/bin/python -m pip install --target deps pathfinding==1.0.18
# vendor_openni中使用厂商对应ARM64运行库，不复制进系统目录。
g++ -std=c++17 -O2 rgbd_bridge.cpp -I/usr/include/openni2 -Lvendor_openni -lOpenNI2 -o rgbd_bridge
g++ -std=c++17 -O2 depth_cloud.cpp -I/usr/include/openni2 -Lvendor_openni -lOpenNI2 -o depth_cloud
```

YOLO11x-Seg HBM SHA256：`edf0df268af23c3b0255a4ef0f7813ee1842fdfaf42c05896ef443ef8892ffac`。suite的Pose模型默认来自板端官方 `/opt/hobot/model/s600/basic/`，运行前检查模型存在。

从[官方OELLM1.0.5文档](https://developer.d-robotics.cc/rdk_s_doc/en/Advanced_development/toolchain_development/LLM_Toolchain/rdk_s600/s100_LLM_Toolchain_v1_0_5)取得SDK并核对许可。解压后找到包含lib/examples/configs的runtime目录：

```bash
/usr/bin/python3 prepare_vlm_runtime.py --runtime /path/to/official/runtime --output runtime_compact
/usr/bin/python3 fetch_foundation_models.py
/usr/bin/python3 fetch_foundation_models.py --verify-only
```

模型合计约3.37GB。默认目录名以 `_ram` 结尾，但**普通目录不是内存盘**。有足够磁盘可存模型；语音ring要求models_whisper_ram确实为tmpfs，防止把原始音频持久写盘。示例两次临时挂载只针对本应用目录，先核对完整路径、无进程使用和可用RAM，不改fstab：

```bash
mkdir -p models_qwen_ram models_whisper_ram
# 在下载之前挂载；挂载已有非空目录会隐藏原文件，勿盲目执行。
sudo mount -t tmpfs -o size=2600m,uid=$(id -u),gid=$(id -g) tmpfs "$PWD/models_qwen_ram"
sudo mount -t tmpfs -o size=1200m,uid=$(id -u),gid=$(id -g) tmpfs "$PWD/models_whisper_ram"
/usr/bin/python3 fetch_foundation_models.py
```

tmpfs占RAM，重启/卸载会丢模型缓存，必须重下；不是常驻安装。建议新板先留足磁盘与RAM再配置，不修改内存分区来强行启动。

```bash
/usr/bin/python3 -m unittest -v test_native_contract test_scene
/usr/bin/python3 product_demo.py --output runs/demo_001 --seconds 900 --capture-seconds 300
# 在笔记本另开终端，BOARD_HOST由开发者配置，不在仓库记录私网地址。
ssh -L 9187:127.0.0.1:9186 sunrise@BOARD_HOST
```

浏览器打开 http://127.0.0.1:9187/ 。点击“看见什么”完成图片问答；点击“听5秒并回答”后说话。低能量明确拒绝；有声不代表有效人声，Whisper可能把背景声转成文字，需人工核对。生成文本不作为可信指令。UI禁止跨域请求、使用随机会话令牌，服务默认900秒结束，非开机自启。

结束后确认无本应用采集/推理进程，再卸载自己的两处tmpfs以释放RAM；不要操作其他项目挂载。所有原始音频仅5秒滚动内存、临时WAV处理后删除；现场图像、转写与答案日志仍可能包含隐私，输出默认留在开发机，不上传公网。

## 仍需团队完成

RGB/深度标定、相机—雷达外参、真实双足足迹与避障可执行性；现场已知口令识别准确率、噪声鲁棒性；多模型长时性能/内存/温度与恢复。当前没有目标距离或到视觉目标的几何路径，不把文本问答包装成导航。

## 上游与许可

复用[D-Robotics Model Zoo](https://github.com/D-Robotics/rdk_model_zoo/tree/rdk_s)、[Orbbec Astra ROS2参考](https://github.com/orbbec/ros2_astra_camera)、[LDROBOT协议/SDK](https://github.com/ldrobotSensorTeam/ldlidar_sdk)、[Vosk](https://alphacephei.com/vosk/models)、[python-pathfinding](https://github.com/brean/python-pathfinding)。SDK、模型、Ultralytics及第三方代码各自许可证不被本项目Apache-2.0取代；仅发布本项目接口/封装，未再分发权重或厂商二进制。使用/商用须分别审查上游许可。
