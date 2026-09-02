# S100 D4 原生与前沿算法审计

## 结论

D4 在 **S100 单主板、离线文件输入、无外设、无控制输出** 的边界内为 `PASS`。`rdk_model_zoo` 的 4 个强制官方样例已在 Nash-e BPU 上实跑；其他前沿候选已锁定 commit，并依据板端依赖和机器人合同分别标为受阻、参考或不适用。受阻项没有被计作部署成功。

## 已部署并实跑

来源：<https://github.com/D-Robotics/rdk_model_zoo/tree/rdk_s>，commit `872748d563c99a490733760c54865b2a70f3499f`。

| 模型 | 离线输入 | 结果 | BPU 时延摘要 |
| --- | --- | --- | --- |
| YOLO26 detect | 官方 `bus.jpg` | 4 个检测，类别 0/5，`PASS` | p50 2.386 ms；p99 15.449 ms |
| ByteTrack + YOLOv5x | 官方 `track_test.mp4` 前 120 帧 | 120/120 帧有轨迹，21 个唯一 ID，`PASS` | 检测器 p99 27.459 ms；整链 p99 75.449 ms |
| Depth Anything V2 | 官方 `furseal.jpg` | 深度输出有限且非恒定，`PASS` | p50 120.539 ms；p99 131.322 ms |
| PointNet part segmentation | 官方 `chair.pts` | 2,776 点、4 个部件标签，`PASS` | p50 1.814 ms；p99 11.450 ms |

Depth Anything V2 只将上游依赖 Torch 的双线性 resize 后处理替换为 OpenCV；推理张量未改。ByteTrack 保留官方检测器与跟踪器，用 SciPy 线性分配和 NumPy IoU 适配板端缺失的 `lap`/`cython_bbox` 扩展；因此只声称流程与合同通过，不声称第三方扩展逐字节等价。

机器证据：`evidence/d4_official_smoke/2026-08-29T18-51-14+08-00/result.json`，清单哈希 `958f845be403d3852bbb87d99e8ea2af4c5ea3e87ee829c1b8f3f266746a0dae`。

## 前沿候选决策

| ID | 仓库 / commit | 决策 | 事实依据 |
| --- | --- | --- | --- |
| F2 | `D-Robotics/mono_edgetam` `0167c87a…` | `BLOCKED_SYSTEM_DEPENDENCY` | 官方支持 S100/S100P，但需要 ament/colcon 与 TROS `dnn_node`、`hbm_img_msgs`、`hobot_cv`、`ai_msgs`；本板均未安装，当前不授权系统安装。 |
| F3 | `D-Robotics/hobot_stereonet` `034c2274…` | `BLOCKED_SYSTEM_DEPENDENCY` | 官方含 S100 和 standalone 路径，但当前无 TROS、colcon、Git LFS；选择性 standalone 重建还需单独验证 S100 运行库合同。D4 的实际 3D 增量由 PointNet 承担。 |
| F3 参考 | `D-Robotics/hobot_centerpoint` `56f2fdf7…` | `BLOCKED_SYSTEM_DEPENDENCY` | 官方支持本地点云回灌，但文档要求安装 TROS CenterPoint/WebSocket 包；当前边界不允许 apt 变更。 |
| F4 | `Improbable-AI/walk-these-ways` `0e7236bd…` | `NOT_APPLICABLE_ROBOT_CONTRACT` | 公共部署是 Unitree Go1 低层控制路径，不是已验证的 S100/萝博头 23 关节部署。此前计划中“官方 S100 流程”的表述已纠正。 |
| F5 | `LeCAR-Lab/ASAP` `df5320cc…` | `AUDIT_ONLY_CONTRACT_MISMATCH` | 公开合同面向 Unitree G1；萝博头 URDF、执行器模型、关节顺序与观测/动作合同尚未取得。 |
| F6 | `D-Robotics/rdk_LeRobot_tools` `326ea043…` | `REFERENCE_ONLY_DATASET_REQUIRED` | 官方只验证 S100 ACT；导出需要真实数据集/检查点，公开板端入口会创建 SO-101 相机与机器人控制环，因此本阶段不运行。 |
| F7 | `nvidia-isaac/WBC-AGILE` `408a27cd…` | `TRAINING_PC_ONLY_PENDING_ROBOT_CONTRACT` | Isaac Lab/Isaac Sim 训练框架，公开验证对象是 Booster T1 / Unitree G1；可在 RTX 4050 上作为未来训练参考，当前不是 S100 部署。 |

完整锁文件：`inventory/s100_d4_frontier_lock.yaml`；机器决策：`config/s100_d4_frontier_decision.json`。板端依赖审计首轮过严地要求 ONNX Runtime 缺失，保留为 `FAIL`；修正为项目隔离环境中实际存在 ONNX Runtime 后，复跑 `PASS`：`evidence/d4_frontier_audit/2026-08-29T19-03-26+08-00/result.json`。

## 当前 RL / 控制定位

D4 没有伪造一个缺少数据集或萝博头合同的“前沿控制部署”。现阶段可运行的 RL/运动基线仍是 D3 锁定的官方策略：`encoder` 与基础 `policy` 在 BPU 上，`policy_attn_enc` 因所有 BPU 精度/时延组合未同时过门而明确回退 CPU。动作始终写文件，不发布控制话题。

## 后续解锁条件

1. 取得萝博头最终 URDF/MJCF、23 关节顺序、执行器/减速器、零位/符号/限位和观测动作合同。
2. 取得真实或仿真的相机/深度/点云标定合同与离线数据。
3. 若要运行 EdgeTAM、StereoNet 或 CenterPoint，单独确认 TROS/colcon/Git LFS 的项目隔离安装或系统包变更。
4. 若要训练 ACT、ASAP 或 WBC-AGILE，先在 RTX 4050 上建立与萝博头合同一致的数据或仿真环境，再导出纯文件 sink 影子输出；实机控制仍需新的硬件安全门。
