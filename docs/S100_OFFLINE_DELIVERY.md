# 萝博头项目：RDK S100 单板离线算法交付

交付日期：2026-08-29
结论：`D0–D6 PASS / U1 BPU PASS / S100_OFFLINE_GATE_COMPLETE=true`

## 交付含义

这块 RDK S100 已完成本阶段获准的单主板离线准备，可作为未来萝博头接入阶段的算法部署基线。该结论严格限定为：使用合成、冻结或录制 fixture，在没有机器人和外设的 S100 上完成 CPU/BPU 推理、导航规划、文件 sink 安全链、故障注入、持续运行和洁净重建。

它不表示真实相机、雷达、IMU、CAN、串口、电机、执行器、导航、行走、急停或整机控制已经验证。未来实机阶段仍必须重新核对 URDF、关节/电机身份、方向、零位、限位、观测/动作顺序、控制周期、通信协议和急停/断电手段。

## 板卡与连接基线

| 项目 | 已验证值 |
| --- | --- |
| 板卡 | RDK S100 / `S100_RDK_V1P0` / Nash-e |
| 算力与内存 | 80 INT8 TOPS；官方 12 GB，Linux 当前可见约 9.3 GiB |
| 系统 | RDK OS 4.0.5-Beta、Ubuntu 22.04、Linux 6.1.158-rt58 |
| BPU Runtime | UCP 3.13.6；`hbm_runtime 0.1.0.post1` |
| ROS | ROS 2 Humble；新项目固定 `ROS_DOMAIN_ID=42` |
| SSH/VNC | 已验证；公共快照不披露网络端点 |
| 板端工程 | `/home/sunrise/workspaces/new_project/roboto_origin_s100` |

SSH、VNC 和显示会话保持启用；没有修改 Wi-Fi、路由、固定 IP、systemd、ROS Domain 0 或冻结的 XRD 资产。

## D0–D6 验收结果

| 阶段 | 结果 | 核心证据 |
| --- | --- | --- |
| D0 | PASS | 实机确认为 S100 / Nash-e / 80 TOPS；系统、runtime、网络、显示、服务和无外设状态完成只读盘点 |
| D1 | PASS | 建立独立工程、环境、安全检查、来源/依赖/模型锁；无系统级安装或服务变更 |
| D2 | PASS | X5 官方 O0–O6 与升级 U1–U3 的 S100 CPU 离线复现全部通过；同 fixture、无设备/控制输出 |
| D3 | PASS | 10 个官方模型：9 个 BPU PASS、1 个明确 CPU fallback；encoder 与 policy 必须 BPU 项通过 |
| D4 | PASS | YOLO26、ByteTrack、Depth Anything V2、PointNet 官方 smoke 4/4；前沿候选完成 commit/合同审计 |
| D5 | PASS | Nav2 20/20 目标；10,000 策略步、40,000 守卫子步、8 类故障 fail-close；30 分钟 BPU 压测通过 |
| D6 | PASS | 全新目录重建并重放 D3/D4；源/副本/重放后哈希一致；无残留进程 |

## 关键数值

- D3 官方模型：9 BPU PASS、`policy_attn_enc` 为 CPU fallback、FAIL=0。fallback 是在 6 个 HBM 候选均未同时通过冻结数值与时延门后做出的显式部署决定；其 CPU p99 约 15.63 ms，仍通过 20 ms 策略门。
- U1 时序占据/流/不确定性升级：OpenExplorer 3.7.0 在 `--network none` 容器中生成 Nash-e HBM，SHA-256 为 `5a0bb873d657fb81ec779fe908ac832de50d7da2c41a56e82fc583b96639f013`。
- U1 板端 A/B：128 组测试、3 输出，平均余弦 `0.9998069839537895`，最差 `0.9969569872868342`；CPU p50 `4.253374 ms`，BPU p50/p99 `0.806851/12.03518641 ms`。原 X5 fixture 与测试 0 字节一致，三个 CPU 输出均复现 X5 基准。
- D4 官方 smoke：YOLO26 产生 4 个检测；Depth Anything V2 BPU p50/p99 约 `120.539/131.322 ms`；PointNet 处理 2,776 点并输出 4 个部件；ByteTrack 的 120/120 帧均产生轨迹、共 21 个 ID。
- D5 离线整链：策略 p99 `12.549 ms`，守卫 p99 `0.154 ms`；NaN、Inf、stale、OOD、动作跳变、越限、丢帧和时间倒退全部被识别并将 4 个守卫输出归零。
- D5 30 分钟压力：`1,633,873` 次 BPU 调用、0 推理错误、180 个以上资源采样，BPU 最高温度 `52.62 ℃`。20 ms 截止期有 32 次瞬时超限（约 0.002%，最大约 26.07 ms），但冻结的 p99 门通过；结果仅代表当前无外设单板环境。
- D6 重放：D3 仍为 9 PASS + 1 CPU fallback，D4 仍为 4/4 PASS；重放没有改变重建资产。

## 前沿算法处理结果

- 已部署并验证：D-Robotics `rdk_model_zoo` rdk_s 锁定版本中的 YOLO26、ByteTrack、Depth Anything V2、PointNet。
- 已锁 commit、但当前板端系统依赖不足：`mono_edgetam`、`hobot_centerpoint`、`hobot_stereonet`。这些项目没有被虚报为部署完成。
- 仅作为训练/移植参考：`rdk_LeRobot_tools`、ASAP、WBC-AGILE。
- `Improbable-AI/walk-these-ways` 的公开合同面向 Go1；它不是 D-Robotics S100 官方 Go2 流程，也不是萝博头合同，因此本阶段标为 NOT_APPLICABLE。

精确仓库、commit、许可证、依赖事实和决策见 `inventory/s100_d4_frontier_lock.yaml`、`config/s100_d4_frontier_decision.json` 与 `docs/S100_D4_FRONTIER_AUDIT.md`。

## 证据与完整性

- 最终机器汇总：`evidence/host/s100_offline_summary_latest.json`，SHA-256 `78ceef1ffff2593754158bf311c31b0e49c72e8ac38efece09a356c3c3b4627e`。
- 板端完整证据包：`evidence/s100/board_final/s100_final_evidence_20260829T1952.tar.gz`，10,526,441 字节，SHA-256 `4e21005309f2a29055cc2d77f314e3a5dbc7ad5ec390aeaa8e4a0cbb3a694795`。
- 解包后逐文件清单锁定 183 个文件；本机已逐项复算并通过。证据包包含 D0–D6、U1 A/B、D3/D4/U1 部署清单和安全/残留检查。
- U1 部署清单 SHA-256：`a523fe4dc60e5803eda4fdc4134af65bfe8ecd0946442c9cd476065e60080615`。

## 主机侧临时工具链处理

本机 Docker Desktop 4.80.0 启动时遇到已知的 Windows AF_UNIX 临时套接字故障。为完成断网 OE 编译，只关闭了可选 Docker Model Runner，并把三个临时 runtime 目录做可恢复重命名；没有执行恢复出厂，也没有删除镜像、容器数据或 Docker 配置。

- 原设置备份：`%APPDATA%\Docker\settings-store.s100-pre-model-runner-disable-20260829.json`
- 临时 runtime 备份：`%LOCALAPPDATA%\Docker\run.stale-20260829-1934`
- 第二次 runtime 备份：`%LOCALAPPDATA%\Docker\run.stale-20260829-1940-model-runner-off`
- Secrets Engine 临时目录：`%LOCALAPPDATA%\docker-secrets-engine.stale-20260829-1935`

这些备份未删除。待 Docker Desktop 修复对应版本问题后，可在 Docker 完全停止时恢复原设置；当前不建议在 4.80.0 上重新启用 Model Runner，因为会复现启动故障。

## 未来实机阶段的硬门

1. 先做无动力物料与电气核对：板卡、电源、三合一板、线束、CAN_H/CAN_L/GND、终端电阻、协议、节点 ID、急停和断电手段。
2. 再做无执行器通信与传感器时间戳/坐标系核对；离线 fixture 合同必须映射到真实 topic 和 frame，且保持 shadow/file sink。
3. 仅在上述门通过后接一个可靠固定或脱载执行器，以保守电流、速度、转矩和角度限制完成最小动作。
4. 最后逐项扩大到多执行器、定位、导航、视觉和策略；每次只扩大一个变量，并以本交付的 X5/S100 离线基线作为回退点。

任何未来 HW 阶段都需要新的明确授权；本交付没有授权或执行真实设备访问。
