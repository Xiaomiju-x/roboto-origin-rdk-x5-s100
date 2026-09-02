# 官方能力基线

记录日期：2026-08-29。官方文档存在历史命名（例如 `atom01_*`）与当前仓库名不一致的情况；实现以当前官方仓库和聚合仓库锁定提交为准，网页用于解释功能与操作背景。

## 机器人和部署基线

官方资料描述的原型机约 1.25 m、34 kg、23 自由度，48 V / 15 Ah，关节峰值转矩最高约 120 Nm，可选 Intel RealSense D435i 和三维激光雷达。部署栈以 ROS 2 为骨架，通过四路 CAN 驱动 23 个电机，以约 200 Hz 低层周期运行，并以 ONNX Runtime 执行策略。

本地官方部署仓库确认：

- `robot.yaml` 定义 23 个 DM 电机，分布于 `can0-can3`。
- HIPNUC IMU 默认设备 `/dev/ttyUSB0`、921600 波特率。
- 策略配置 `dt=0.004`、`decimation=5`，对应 200 Hz 控制采样和 50 Hz 策略更新。
- 主要 ROS 接口包括 `/joy`、`/cmd_vel`、`/joint_states`、`/imu`、`/action`，以及电机初始化、停机、清错、读状态、零点和推理启停服务。

注意：零点写入和电机初始化属于高风险动作，只有在上电门禁完成且用户确认后才能进入相应测试步骤。

## 强化学习与运动策略

官方训练仓库声明 Python 3.11、Isaac Sim 5.1.0 和可变的 Isaac Lab `main`。锁定的 RoboParty 聚合提交为 `92008a6317d6d0efe8b58abc2fb3c630c75992c0`（2026-07-26）；项目为可复现性选择该日期之前 Isaac Lab `main` 的最后提交 `b0542fe2d45bf91c4e1d9ef6952b9c709c80b4e8`（2026-07-24），而不是继续跟随浮动分支。该提交的官方 pip 文档仍指定 Isaac Sim 5.1.0/Python 3.11，并包含 RoboParty 脚本需要的 `handle_deprecated_rsl_rl_cfg`。项目同时锁定训练仓库子模块中的 `rsl_rl` 提交 `6986d4d1b9fbab96fb61d51f07de419bc95432f1`。当前仓库可识别的主要任务和部署策略为：

| 类别 | 训练/部署基线 | 备注 |
| --- | --- | --- |
| 基础行走 | `RPO-Flat`、`RPO-Rough`、default | 盲走基线 |
| 模仿学习 | `RPO-AMP`、AMP | 动作先验 |
| 地形感知 | `RPO-AttnEnc`、AttnEnc | 高程感知输入 |
| 中断恢复 | `RPO-Interrupt`、Interrupt | 扰动/中断恢复 |
| 动作跟踪 | `RPO-BeyondMimic`、BeyondMimic | 含 wave/dance 等动作 |
| 起身 | `RPO-Getup-Mimic`、Getup | 恢复策略 |
| 跑酷 | `RPO-Parkour`、Parkour | 深度感知策略 |

训练仓库还包含 MuJoCo sim2sim、动作重定向和策略导出路径。当前 Windows 主机已完成隔离环境安装、CUDA/ONNX/MuJoCo 有限回放，并让上述 8 个 Isaac 任务全部完成 1 环境、1 次迭代、checkpoint、两步 headless 回放和再导出。验收只承诺可重复的短闭环，不把 6 GB 显存主机上的缩放冒充官方 4096/8192 环境长周期收敛；完整版本锁和证据见 `inventory/host_training_lock.yaml` 与 `docs/OFFICIAL_OFFLINE_ACCEPTANCE.md`。

## 视觉基线

官方“视觉”可落地基线是深度感知链路，而不是通用视觉识别：

1. D435i 以深度流工作，仓库配置为 480×270、60 FPS。
2. 深度图经裁剪/预处理为 64×36。
3. ONNX 深度编码器产生 128 维观测。
4. 通过 `/depth_obs` 输入 Parkour 策略，配置使用 8 帧历史。

后续目标检测、分割或语义导航必须标注为“升级能力”，不能计入官方视觉基线 PASS。

## 导航基线

官方导航包以 FAST-LIO2（ESKF + ikd-tree）完成三维定位，并通过二维 Nav2 适配层提供：

- 点云地图 PCD 到二维 PGM 的转换。
- 连续巡航与精确航点模式。
- ROS 2 Humble、Ubuntu 22.04 环境。
- Livox MID-360 或兼容雷达及 IMU；可选 NLink UWB。

导航仓库仍含历史 `atom01` 名称。复现时保存其原始接口证据，再在项目适配层统一命名，避免直接改官方副本。

## 版本原则

- `inventory/source_manifest.yaml` 中的 `baseline_pin` 是复现用版本。
- `observed_head` 是 2026-08-28 观察到的升级候选。
- 两者不一致时先完成 `baseline_pin`，再单变量评估 HEAD；不得直接把 HEAD 当成已验证基线。

## 官方来源

- 项目文档：<https://roboparty.com/roboto_origin/doc>
- 聚合仓库：<https://github.com/Roboparty/roboto_origin>
- 部署：<https://github.com/Roboparty/roboparty_deploy>
- 训练：<https://github.com/Roboparty/roboparty_train>
- 导航：<https://github.com/Roboparty/roboparty_navigation>
- 机器人模型：<https://github.com/Roboparty/rpo_description>
- 动作重定向：<https://github.com/Roboparty/GMR>
