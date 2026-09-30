# 验证结果与声明边界

更新时间：2026-09-30

最新[S600外挂算法结果与复现](../apps/s600_coprocessor/README.md)：团队X5动作基线已验证，新增三传感器/现代BPU模型已运行；下面表格是9月2日离线阶段快照，不再表示当前硬件从未接入。几何导航及现场语音准确率仍待验。

本页只汇总已经由机器证据关闭的门。下载、构建、主机运行或单次 smoke 均不会自动升级为板端 PASS。

## 状态总览

| 阶段 | 状态 | 已验证内容 | 明确未验证内容 |
| --- | --- | --- | --- |
| X5 官方 O0–O6 | PASS | 来源/模型合同、CPU/CUDA/X5 ONNX、合成深度、PCD→PGM、Nav2 规划、FAST-LIO2 回放、有限 Isaac 训练/play/export | 真实传感器、执行器、整机闭环与官方大规模训练收敛 |
| X5 U1–U3 | PASS | 时序 BEV、可信动作守卫、KISS-ICP 合成 A/B | 真实点云、真实观测分布、控制器接入 |
| S100 D0–D6 | PASS | 只读盘点、隔离工程、CPU 复现、Nash-e BPU、官方视觉 smoke、故障注入、30 分钟压力、洁净重建 | 机器人、相机、雷达、IMU、CAN、串口、电机、导航和行走 |
| 三板 YOLO shadow 迁移 | FUNCTIONAL PASS | X5 原生视觉基线；S100/S600 YOLO11n 真 BPU；统一 file-only guard | 严格分数等价、真实相机、真实机器人与控制闭环 |
| HW0–HW5 | NOT STARTED | 无 | 全部真实机器人阶段 |

## X5 官方离线基线

- 10 个官方 ONNX 模型在主机与 X5 上完成确定性非零输入检查；输出维数、有限值与数值对照通过。
- 官方深度链使用确定性合成 Z16 流完成下采样、裁剪、8 帧历史、128 维编码和 reset 路径。
- Nav2 在离线 PGM 上生成 457 位姿、11.4805 m 路径，并完成项目进程清理。
- FAST-LIO2 使用 4,060 点合成 Ouster 点云和 200 Hz IMU 回放；最终 65 帧 odometry、30 帧 registered cloud、6 帧 path，静止最大位移 3.111 mm。
- 8/8 有限 Isaac 任务完成 1 环境、1 次学习迭代、checkpoint、2 步 play 与导出验收；这不是收敛性声明。

## X5 U1–U3

### U1 时序占据、流与不确定性

- 32,286 参数的小型时序网络从零训练。
- future occupancy IoU：0.8774；persistence baseline：0.1544。
- 动态 flow EPE：0.1755；zero-flow baseline：1.1956。
- X5 ARM CPU 100 次推理中位：15.52 ms；三路输出有限并与主机对齐。

### U2 可信动作守卫

- 覆盖非有限值、陈旧观测、低置信度/OOD、动作幅值与跳变等故障。
- 五类冻结故障 5/5 fail-close；有限值+裁剪基线为 0/5。
- 守卫是离线纯函数/状态机，不含 publisher 或设备访问。

### U3 KISS-ICP

- 锁定 KISS-ICP v1.3.0 / commit `b16835283aee62f7d5e2bdf6c1c3bb2930de74ff`。
- 与 FAST-LIO2 使用字节一致的 4,060 点 XYZ 几何。
- 静态回放零漂移；4 cm/帧合成运动累计 1.36 m，终点误差约 0.08 µm。

## S100 D0–D6

| 门 | 结果 | 摘要 |
| --- | --- | --- |
| D0 | PASS | RDK S100 / Nash-e / 80 INT8 TOPS；系统、runtime、ROS、存储与无外设状态只读盘点 |
| D1 | PASS | 独立工程、环境、安全检查、来源/依赖/模型锁；无系统级安装或服务变更 |
| D2 | PASS | X5 官方 O0–O6 与 U1–U3 的同 fixture CPU 离线复现 |
| D3 | PASS | 10 个官方模型：9 BPU PASS、1 明确 CPU fallback、0 FAIL |
| D4 | PASS | YOLO26、ByteTrack、Depth Anything V2、PointNet 官方 smoke 4/4；前沿候选按许可证与依赖诚实分流 |
| D5 | PASS | Nav2 20/20 目标；10,000 策略步、40,000 守卫子步、8 类故障；30 分钟压力 |
| D6 | PASS | 全新目录重建并重放 D3/D4；哈希一致，无残留进程 |

关键指标：

- 必须 BPU 的 encoder 与 policy 均通过；`policy_attn_enc` 在 6 个 HBM 候选未同时通过冻结数值/时延门后锁定为 CPU fallback，其 CPU p99 约 15.63 ms。
- U1 Nash-e A/B：128 组、3 输出；平均 cosine 0.999806984，最低 0.996956987；CPU p50 4.253 ms，BPU p50/p99 0.807/12.035 ms。
- D5 策略 p99 12.549 ms，守卫 p99 0.154 ms；8 类故障均触发 fail-close。
- 30 分钟压力共 1,633,873 次 BPU 调用、0 推理错误，最高温度 52.62 ℃。20 ms 截止期有 32 次瞬时超限（约 0.002%），冻结 p99 门通过。

脱敏、机器可读摘要位于 `evidence/summaries/`。公开摘要中的网络端点和本机路径已经移除；完整原始证据只保留在受控的本地交付面。

## X5 → S100 → S600 YOLO shadow 增量

- X5 先用已验证的 YOLO26s+ByteTrack 重跑原生基线：3/3 CSV 字节一致，78 条轨迹、4 个 ID，BPU 退出空闲。
- X5 板载 modified YOLO11 与旧跟踪器组合产生零面积框，几何门失败并停止使用；没有伪装成 PASS。
- S100/S600 使用相同 YOLO11n、相同图像、相同后处理与 guard；各完成 10 次确定性真实 BPU 推理，均得到 4 个 person、1 个 bus 和 file-only `STOP_CANDIDATE`。
- S100 BPU P50 3.402 ms；S600 BPU P50 1.594 ms。两者最差框 IoU 0.9655，功能事件一致。
- 严格分数差冻结门为 0.05，实测最大差 0.05157，因此 `S100_S600_YOLO11_STRICT_SCORE_PARITY_FAIL`；只关闭功能/安全事件迁移门。
- 全程未使用相机或机器人外设，未发送运动命令。详见 [S600_YOLO11_MIGRATION.md](S600_YOLO11_MIGRATION.md)。

## 不得扩大解释

- `S100_OFFLINE_GATE_COMPLETE=true` 仅表示单主板、无外设、离线算法部署就绪。
- 任何 HBM/BIN 的生成都不等于真实 BPU 数值 PASS；任何 BPU PASS 也不等于机器人控制 PASS。
- 现有 `cmd_vel`、动作或策略输出只写入离线 fixture/shadow/file sink，不连接 CAN、串口、电机或控制器。
- S100 的算力提升不能替代 X5 原生机器人硬件基线；真实硬件必须先在 X5 分级关闭门，再开始 S100 适配。
- S600 的离线算法 PASS 只增加算法候选，不授权跳过 X5/S100 的真实机器人顺序或直接接管控制。
