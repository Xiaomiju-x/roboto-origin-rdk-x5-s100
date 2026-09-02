# X5 无设备前置运行验证

执行日期：2026-08-28

结果：**PASS（仅 X5 无设备前置工作）**

## 结论边界

X5 已完成项目隔离依赖、官方 deploy/navigation 静态构建、10 个官方 ONNX 模型的合成输入执行、官方 PCD→PGM 离线转换，以及导航安装资产的静态校验。整个过程没有启动 ROS 算法节点，没有访问相机、雷达、IMU、`/dev/F407` 或执行器，`can0` 始终为 `DOWN`。

该结果不等于官方算法已完整复现。深度图真实预处理、MuJoCo sim2sim、传感器数据流、TF、定位、规划、50/200 Hz 控制周期和机器人动作仍需按测试矩阵逐项实跑。URDF/MJCF 限位与 torso 零偏冲突仍阻止实机部署。

## ONNX 合成输入执行

独立探针直接使用官方部署包内的 ONNX Runtime `1.21.0` ARM64 CPU 库。每个模型使用全零 `float32` 张量，先预热 1 次，再测量 3 次；不创建 ROS 节点或机器人接口。10 个模型全部成功加载和执行，三次输出均为有限值，策略输出为 23 个元素，深度编码器输出为 128 个元素。

| 模型 | 运行输入 | 输出元素 | 3 次平均时延（ms） | 结果 |
| --- | --- | ---: | ---: | --- |
| `policy` | `1×780` | 23 | 0.953 | PASS |
| `policy_amp` | `1×234` | 23 | 0.378 | PASS |
| `policy_attn_enc` | `1×577` | 23 | 3.879 | PASS |
| `policy_dance0` | `1×121` | 23 | 0.294 | PASS |
| `policy_dance1` | `1×121` | 23 | 0.305 | PASS |
| `policy_getup` | `1×121` | 23 | 0.289 | PASS |
| `policy_interrupt` | `1×790` | 23 | 1.013 | PASS |
| `policy_parkour` | `1×752` | 23 | 2.729 | PASS |
| `policy_wave` | `1×121` | 23 | 0.289 | PASS |
| `depth_encoder` | `1×8×18×32` | 128 | 1.327 | PASS |

这些数字只是当前板卡、CPU 执行提供者、零输入和 3 次样本的可用性快照，不能作为真实数据精度、BPU 性能、实时截止期或整机效果结论。

机器证据：`evidence/x5/onnx_runtime/onnx_synthetic_20260828T222405+0800.json`，SHA-256 `bd4409420cfca18c3fd4eb5e10acbab24095eca49f8336c160a7180ad2e8a05a`。

## 官方 PCD→PGM 离线转换

项目内新增 Pillow `11.3.0` 与 SciPy `1.15.3` ARM64 哈希锁定包，安装到 Open3D 隔离 venv；未修改系统 Python。安全包装器调用锁定官方 `pcd2pgm.py` 的原始算法函数，并显式关闭调试 PCD 与高程图片输出，所有结果只写入项目证据目录。

- 官方转换脚本 SHA-256：`5b84277671cf941cd0821bef20d53ae5a596355a9d64e2344e7da67cb0a6ccab`。
- 输入 `map_ikdtree.pcd`：9,999,798 字节，SHA-256 `4443430c0ceced4effac6514e3dd743fd27411e1fe51862ed0ffdac2cf9612ca`。
- 高度过滤后点数：139,211。
- 输出：`232×524`、`0.05 m/pixel`，耗时 34.581 秒。
- 栅格：free 25,435、occupied 7,386、unknown 88,747；PGM 只包含官方约定的 `254/0/205` 三种灰度值。
- PGM 与 map YAML 的尺寸、相对图像路径、分辨率、原点、阈值和 SHA-256 均已机器校验。

证据目录：`evidence/x5/navigation_offline/20260828T223859+0800/`。

## 导航静态资产校验

在不执行 launch 文件的前提下完成：

- 4 个安装后 Python launch 文件 AST 解析；
- 12 个安装后 YAML 文件安全解析；
- 官方地图、两份 PCD 和两个核心节点可执行文件的存在性与哈希检查；
- adapter 合同确认：`robot_0/odometry`、`map/odom/base_link`、10 Hz TF。

结果为 PASS、0 failure，同时保留 4 个可移植性警告：四份 Nav2 参数仍引用开发者旧路径 `/home/zyq/roboparty/roboparty_navigation/src/robots_localization_ros2/map/roboparty4.yaml`。本阶段不修改官方源码或安装副本；后续应在项目自有 X5 配置层覆盖地图路径，并通过实际 Nav2 启动和规划验证。

## 可重复入口

```bash
cd /home/sunrise/workspaces/new_project/roboto_origin
source scripts/env_x5_deps.sh
bash scripts/board/build_x5_dependencies.sh navigation_python
bash scripts/board/run_x5_onnx_synthetic_probe.sh
bash scripts/board/run_x5_navigation_offline_probe.sh
bash scripts/board/capture_x5_offline_prework_evidence.sh
```

最终综合快照：`evidence/x5/prework/offline_prework_20260828T224305+0800.txt`，SHA-256 `712f2f74c09f332b4807c940b8d7a165fee263f1749997ca56a0aa0253c35106`。快照再次确认 Domain 42、五个官方仓库干净、四个冻结服务 inactive、`can0 DOWN` 且无官方运行节点。

## 项目顺序

用户于 2026-08-28 再次明确长期顺序：

1. 在 X5 上完整复现 RoboParty 官方算法基线；
2. 再评估外部只读 XRD 开源快照中的可复用算法，建立相对官方基线的升级与回退；
3. 最后进入 S100 独立适配与前沿算法扩展。

本页只记录 2026-08-28 的早期无设备前置门，不能作为 S100 上电授权。后续更严格的 O0–O6 官方算法离线验收已于 2026-08-29 全部 PASS，当前状态见 `docs/OFFICIAL_OFFLINE_ACCEPTANCE.md`。这只打开 XRD/联网前沿算法升级门；升级在 X5 离线 A/B 验收完成前仍不得要求 S100 上电。
