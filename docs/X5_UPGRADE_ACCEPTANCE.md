# X5 XRD/联网算法升级离线验收

更新时间：2026-08-29

## 范围与总结果

本轮只在 RTX 4050 笔记本和 X5 单板执行确定性合成数据训练/推理。没有连接或打开相机、雷达、IMU、CAN、电机和 `/dev/F407`，没有发布机器人控制 topic。聚合证据
`evidence/host/x5_upgrade_summary_latest.json` 为 `overall=PASS`、
`x5_upgrade_gate_complete=true`、`s100_gate_open=true`。

## 来源

- XRD 外部只读开源快照：提交 `6c150dc3ea964b0617ede87f5b76a928876c693b`；本机路径不进入公共仓库。
- KISS-ICP：官方 v1.3.0，提交 `b16835283aee62f7d5e2bdf6c1c3bb2930de74ff`，MIT。
- WBC-AGILE、ASAP、Depth Anything V2 Small、YOLOE-26 的审计与未选原因见 `X5_UPGRADE_SELECTION.md` 和 `config/upgrade_candidate_matrix.yaml`。

项目只重新实现 XRD 中的时序 BEV、未来占据/流、conformal/OOD 和 ShadowGuard 思路；没有复制其权重、服务、地图、机器人几何或运行配置。

## U1：时序占据、流和不确定性

项目自有 `TinyTemporalOccFlow` 使用固定 `1x4x32x32` 历史 BEV，输出三时域占据 logits、二维流和变化区不确定性 logits，共 32,286 参数。以 seed `20260829` 在 RTX 4050 上训练 24 epoch；模型和非零测试向量的哈希在结果 JSON 中锁定。

| 指标 | 候选 | 基线 |
| --- | ---: | ---: |
| 未来占据 mean IoU | 0.8774 | last-frame persistence 0.1544 |
| 动态流 EPE | 0.1755 | zero-flow 1.1956 |
| 变化区/稳定区不确定性均值 | 0.9208 / 0.0244 | 不适用 |
| 95% conformal 独立测试覆盖 | 96.48% | 不适用 |

ONNX opset 17 在主机 CPU、关闭 TF32 的 CUDA provider 与 PyTorch 严格对照通过。默认 CUDA TF32 曾产生最大 0.018 logits 差异，因此保留首次 FAIL 证据并把 `use_tf32=0` 固化在验收脚本；没有靠放宽门限判过。

X5 使用 ONNX Runtime CPU provider 执行同一非零向量 100 次，中位时延 15.52 ms，三路输出全部有限并与主机参考一致。板端没有发现 `hb_mapper`/`hbdk4-cc`，且 CPU 已超过 50 Hz 目标，因此本轮不伪造 BPU 转换结论。

## U2：可信度与动作守卫

候选检查输入形状、NaN/Inf、观测陈旧、低可信度、conformal OOD、动作限值/总范数和单步变化，并在故障时确定性归零、短暂锁存。它只是离线纯函数，没有 publisher 或运动权限。

- 故障：NaN、陈旧观测、OOD、单关节突变、越限，共 5/5 检出；
- finite+clamp 基线：0/5 完整停机；
- 独立净输入回放：99.55% 元素原样保持；
- 非停机路径最大单步变化：0.08；
- 主机与 X5 的事件序列和指标字节级数值一致。

第一次突变 fixture 同时触发总范数门，导致“期望 slew、实际 action_norm”的测试 FAIL；收窄为单关节突变后最终 PASS。两次证据均保留。

## U3：KISS-ICP 影子定位

上游源码在板端以 Linux 原生干净检出构建，构建前后均核对精确提交。上游回退下载的 Sophus 1.24.6 要求 CMake 3.24，而 X5 是 3.22.1；最终使用官方阶段已经位于项目隔离目录的 Sophus 1.22.10，并在新 build/install 目录重试，未改系统包和上游源码。

最终输入与 FAST-LIO2 验收的 XYZ fixture 字节一致：4,060 点、SHA-256
`c0a46df19753a67db4be24e4ef842daeaa06ad069f4e44344c048d733d7972e3`。静态回放接收 36 帧并输出 35 帧有限里程计，最大漂移为 0。第二组把传感器沿 +X 每帧平移 4 cm，期望终点 1.36 m，估计 1.36000008 m，终点误差约 0.08 µm、轨迹 X RMSE 约 0.05 µm、最大横向误差约 0.003 µm。该理想无噪声序列用于证明估计器确实响应运动，不代表真实雷达精度。FAST-LIO2 同静态几何记录为 3.111 mm；由于后者另有 IMU/Ouster 字段且估计器不同，这两个值只作诊断，不作优劣排名。

第一次用 `ros2 run` 启动时包装器退出但子进程残留，门禁判 FAIL并记录 PID；明确终止后改为直接执行已安装二进制。最终 SIGINT 正常结束、无需强杀、无残留，TF 发布和 debug cloud 均关闭。

## 板端位置与回退

所有新内容均在 `/home/sunrise/workspaces/new_project`：

- 源码/脚本/模型：`src/roboto_origin_upgrade`；
- KISS-ICP 安装：`install/roboto_origin_upgrade/kiss_icp_v130`；
- 构建：`build/roboto_origin_upgrade/kiss_icp_v130_sophus122`；
- 证据：`logs/roboto_origin_upgrade`。

没有建立 systemd 服务、开机项、系统包或控制入口。逻辑回退是不要 source KISS overlay、不要运行三个候选脚本；官方 O0–O6 资产完全未覆盖。删除这些项目资产属于破坏性操作，本轮没有执行。

## 最终安全状态

X5 最终 SSH/VNC 为 active；四个冻结 XRD 服务为 disabled/inactive；`can0` 为
DOWN/STOPPED、RX/TX 零；没有候选算法残留进程。因此 X5 离线升级阶段关闭，下一步是用户断开 X5并给 S100 上电，再进行 D0 只读盘点。
