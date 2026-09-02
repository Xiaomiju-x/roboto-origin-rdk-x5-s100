# RDK S100 单板离线算法阶段计划

状态：`S100_POWER_ON_REPORTED / D0–D6_PASS / U1_BPU_PASS / S100_OFFLINE_GATE_COMPLETE`
制定日期：2026-08-29

用户已于 2026-08-29 明确回复“确认执行 S100 计划（D1–D6）”。D0–D6 单板离线工程创建、项目内依赖、编译、模型转换和算法验证均已完成；授权从未扩大到系统级安装、外设或机器人控制。

## 当前目标与边界

本阶段只完成笔记本 RTX 4050 训练/导出与 S100 单主板离线部署。S100 不接机器人、三合一板、CAN、执行器、相机、雷达、IMU 或其他外设；不发送任何控制命令，不烧录镜像/固件，不改网络、SSH/VNC、系统服务、ROS Domain 0 或冻结资产。

X5 官方 O0–O6 与 X5 升级 U1–U3 已全部 PASS，S100 门已打开。S100 必须先复现这些相同能力，再增加 S100 原生和前沿算法；不得以“算力更高”替代同输入、同指标的 A/B 验收。

## 官方参数基线与实机待核对项

下表来自地瓜机器人当前官方硬件说明。它只用于制定盘点与部署路线，最终以这块实机的 `/sys/class/boardinfo/soc_name`、`rdkos_info`、`/etc/version`、内存和 runtime 证据为准。

| 项目 | RDK S100 | RDK S100P | 本项目影响 |
| --- | --- | --- | --- |
| 产品型号 | KS1E55Y | KS1P75Y | D0 读取实机标识，不凭外观判断 |
| SoC/BPU | S100E / Nash-e | S100P / Nash-m | OE 编译目标必须与实机一致 |
| INT8 算力 | 80 TOPS | 128 TOPS | 官方是 80/128，不是 80/120 |
| CPU | 6× Cortex-A78AE，最高 1.5 GHz | 6× Cortex-A78AE，最高 2.0 GHz | CPU 基线和 ROS/定位时延分别测量 |
| 内存 | 12 GB LPDDR5 | 24 GB LPDDR5 | 决定模型并发、校准集和持续运行余量 |

系列共有的官方参数：

- 1× Nash BPU、ARM Mali-G78AE GPU、4× Cortex-R52+ MCU（1× DCLS、1× Split-Lock）。
- 96-bit LPDDR5，最高 6400 Mbps；板载 64 GB eMMC；M.2 Key M 为 PCIe Gen3×1 SSD 接口。
- 4× USB 3.0 Type-A（Host），1× USB 2.0 Type-C（烧录与主域/MCU 调试，不是全功能 Type-C）。
- 1× HDMI Type-A，最高 2560×1440@60 Hz；相机扩展口提供 3×4-lane MIPI CSI-2。
- 2× 千兆 RJ45；`eth1` 的官方管理端点见厂商文档，`eth0` 由 DHCP/用户配置。公共快照不记录实际端点。
- 12–20 V DC 输入，包装适配器 90 W，最大负载设计上限 150 W；官方工作温度 0–45 ℃。
- MCU 扩展板可提供 CAN5–CAN9 共 5 路 CAN FD（最高标称 8 Mbps），但本单板阶段只识别软件/接口存在性，不启用、不接线、不收发。

当前官方 S100 资源中心列出的系统镜像为 `RDKS100-V4.0.5_20260507`；对应发布说明是 Ubuntu 22.04、Linux `6.1.158-rt58`、OpenExplorer `3.7.0`。这块板可能安装不同版本，工具链必须等 D0 实测后再锁，不能先装最新版覆盖板端。

## 当前网络与 VNC

2026-08-29 的只读检查确认 SSH 22 与 VNC 5900 可达，并在板端核对了设备身份和 x11vnc 状态。另一个管理接口在盘点时 link down，未作为已验证入口。公共快照不披露当前或历史私网地址；复现者必须从目标板本地读取自己的端点，不能复制项目维护者的网络配置，也不得通过改网强行验证。

## 2026-08-29 实机 D0 结果（PASS）

- 实机为 `S100_RDK_V1P0`、Board ID `0x6A86`、`soc_name=S100`，因此确认为 **RDK S100 / S100E / Nash-e / 80 TOPS**，不是 S100P。
- 6× Cortex-A78AE，频率 1.125–1.5 GHz；官方物理内存 12 GB，Linux 当前可见 9.3 GiB、盘点时 available 7.4 GiB，无 swap。
- RDK OS `4.0.5-Beta`、Ubuntu `22.04.5`、实时内核 `6.1.158-rt58-DR-4.0.5-2603191535-gf29a43-g94bc27`。
- `hobot-dnn 4.0.5-20260211103825`、`hbm-runtime 0.1.0.post1`、UCP/`hrt_model_exec 3.13.6` 已安装；板端没有 `hb_compile`、`hbdk4-cc`、`hb_mapper`，所以 OE 编译必须放在匹配版本的主机工具链中。
- ROS 2 Humble 位于 `/opt/ros/humble`，source 后可见 185 个包。公共 `env.sh` 只设置 Domain 42 和端口范围，没有 source ROS；D1 应新增 S100 专用环境入口，不修改公共文件。
- 59.6 GB eMMC；根分区约 45 GB，已用 28 GB、可用 16 GB。`new_project` 目前只有 Lesson 09 HDMI 工程，尚无 `roboto_origin_s100`，`9100-9199` 无监听端口。
- HDMI-1 当前为主输出 `1280×720@60 Hz`；SSH 与 x11vnc 均 `enabled/active`，5900 监听所有 IPv4/IPv6 地址。当前 WLAN VNC 已闭环；`eth1` 固定地址存在但链路为 down，直连端点留待物理链路建立后复核。
- 只有 USB root hub，没有外接 USB 设备、串口节点或 Linux CAN 网卡；未发现 `/dev/F407`，未打开任何设备。
- 盘点时 BPU 利用率 0%，BPU 温度约 47.5 ℃；但系统 1 分钟负载约 9.75，GNOME Shell 瞬时约 245% CPU，更新管理器约 14%。此状态不能作为性能验收基线；确认执行后先只读复测空闲状态，不擅自终止桌面或更新进程。

机器可读证据：`evidence/s100/d0_readonly_snapshot_20260829.json`。D0 已关闭；D1–D6 已获用户确认并从 D1 开始执行。

## 阶段顺序与硬门

```text
D0 实机只读盘点
  -> D1 独立工程、来源锁与回退
    -> D2 X5 官方全基线 + U1-U3 的 S100 CPU 复现
      -> D3 Nash BPU 转换、量化与数值/时延 A/B
        -> D4 S100 原生与前沿算法
          -> D5 纯离线整链、故障注入与持续运行
            -> D6 干净重建与交付
```

每一门只有机器证据为 PASS 才进入下一门。保留失败记录；不通过放宽阈值、删证据或修改 X5 基线来“过门”。

### D0：SSH 只读盘点

1. 仅使用本机已确认的目标板端点；核对 SSH/VNC、hostname/IP，不修改网络。
2. 完整读取 `/home/sunrise/workspaces/new_project/README.md`（若存在），加载 `env.sh`，核验 `ROS_DOMAIN_ID=42`。
3. 记录板型、`soc_name`、CPU/内存、内核、Ubuntu/RDK、`rdkos_info`、BPU/OE/runtime、ROS/TROS、Python/CMake/GCC、磁盘、温度/频率接口、服务、端口和设备节点。
4. 只枚举 USB/串口/CANHAL/CAN2IPC；不打开设备，不启动驱动/控制节点，不终止现有进程。

验收：精确确定是 S100 80 TOPS/12 GB 还是 S100P 128 TOPS/24 GB，并输出只读快照、X5/S100 差异表、VNC 证据和安全状态 JSON。

### D1：独立、可回退工程

1. 只在 `/home/sunrise/workspaces/new_project/roboto_origin_s100` 建立 `src/build/install/logs/models/data/evidence`。
2. 本地建立 `config/platform/s100/`、S100 能力/来源/依赖锁、环境脚本和安全检查；不覆盖 `config/platform/x5/`。
3. 上游仓库锁精确 commit、子模块、许可证和模型 SHA-256；上游只读，补丁放项目适配层。
4. 固化与 X5 字节一致的 fixture、输出合同、一键重建和“停止加载 overlay 即回退”的路径。

验收：没有系统安装、systemd/网络/设备/启动项修改；Domain 42；目录无覆盖；来源、依赖、模型可追溯。

### D2：X5 能力的 S100 CPU 完整复现

按 X5 已通过顺序复跑：

- 9 个官方运动/RL 策略 ONNX、1 个官方深度编码器；
- 官方合成深度历史链、PCD→PGM、Nav2 合成规划、FAST-LIO2 合成点云/IMU；
- U1 时序占据/流/不确定性、U2 可信动作守卫、U3 KISS-ICP v1.3.0。

验收：使用与 X5 同哈希输入；shape/finite/输出合同通过；记录 Host/X5/S100 数值差、p50/p95/p99、CPU/内存和退出状态；ROS 无残留、无控制 topic 外发。D2 只证明 CPU 功能复现。

### D3：Nash BPU 转换与部署

1. D0 确认 `s100/nash-e` 或 `s100p/nash-m` 后，选择与板端 runtime 匹配的 OE 工具链。
2. 逐个执行 ONNX 检查、校准集冻结、PTQ/混合精度或必要的 QAT、`.hbm` 编译、哈希、板端加载与推理；S 系列 NV12 双输入适配与 X5 分开实现。
3. 同一测试集对比 Host 浮点 ONNX、S100 CPU 和 S100 BPU。参考官方 Model Zoo，余弦相似度目标 `>=0.999`，任何输出 `<0.99` 判 FAIL；动作/安全模型另设冻结的最大误差与决策一致性门。
4. 算子不支持时，先做可审计等价改写/混合量化/CPU-BPU 分区；仍失败则记录 `CPU_FALLBACK`，不得声称 BPU PASS。
5. 50 Hz 策略端到端 `p99 <= 20 ms`；200 Hz mock 守卫链 `p99 <= 5 ms`。预热至少 20 次、统计至少 100 次，同时记录纯 BPU和预/后处理总时延。

### D4：S100 原生与前沿算法升级

来源顺序为 D-Robotics 官方 → 作者主仓库/论文代码 → 许可证清楚且依赖可锁定的社区实现。笔记本 RTX 4050 负责训练/蒸馏/导出，S100 负责离线推理。

首批候选矩阵：

| 类别 | 候选 | 计划定位 |
| --- | --- | --- |
| 官方 Nash 视觉 | `rdk_model_zoo` 的 YOLO26、ByteTrack、Depth Anything V2、PointNet | 必做官方 smoke，使用示例图/公开离线集 |
| 前沿视频分割/跟踪 | D-Robotics `mono_edgetam`（EdgeTAM） | 优先升级候选；官方 S100/S100P HBM 管线，离线视频 A/B |
| 3D 感知 | `hobot_bev`、`hobot_centerpoint`、`hobot_stereonet` | 依据 SKU/工具链/离线数据选一项，不连接真实相机或雷达 |
| 定位/导航 | Nav2 + KISS-ICP；保留 FAST-LIO2 对照 | 与 X5 同 fixture 的跨板一致性和性能升级 |
| RL/运动 | 上游 `Improbable-AI/walk-these-ways` | 仅审计；其公开合同面向 Go1，并非 S100 官方方案或萝博头适配 |
| 人形 sim2real | ASAP | 先审计；官方 S100 页面目前不足以证明完整优化实现已可重复开放 |
| 模仿学习 | D-Robotics `rdk_LeRobot_tools` ACT | 作为 Nash 转换范式；动作只写文件，不运行会连接 SO-101 的控制入口 |
| 全身控制 | WBC-AGILE | 审计项；环境和 23 关节合同未闭合前不部署 |

Ultralytics、Depth Anything 各尺寸权重、公开数据和每个派生仓库的许可证逐项记录；不会把外层 Apache-2.0 自动延伸到第三方权重。D0 后再锁实际 commit，并从视觉、3D/导航、RL/控制中各选择最多一个可诚实部署的增量候选做 A/B。

### D5：纯离线整链与安全故障注入

1. 以合成/录制数据和 mock transport 串联感知→定位/导航→策略→U2 守卫→文件 sink；sink 只记录，不创建 CAN/串口/电机连接。
2. 在 Domain 42 验证 50 Hz 策略与 200 Hz 守卫，注入 NaN/Inf、stale、OOD、动作跳变、越限、丢帧和时间倒退，全部要求 fail-close。
3. 策略至少连续离线推理 10,000 步；代表性 BPU 模型不少于 1,000 次并运行 30 分钟，记录截止期遗漏、CPU/BPU/内存、温度/降频、错误计数与退出状态。
4. Nav2 至少完成 20 个合成目标，`cmd_vel` 必须重映射到捕获 topic/文件；不得写设备节点或真实控制 topic。

验收：无 NaN/Inf、无设备访问、无控制输出、无残留进程，SSH/VNC 仍可用。温度结果仅代表无外设单板场景。

### D6：重建、聚合与交付

从锁文件在干净项目路径重建并复跑代表 fixture，生成 `evidence/host/s100_offline_summary_latest.json`。只有 D0–D5 必选门均通过才写 `s100_offline_gate_complete=true`，并交付版本、命令、模型哈希、A/B、CPU/BPU 回退、限制和未来实机清单。

该 PASS 只表示“单板离线部署就绪”，不表示真实相机、雷达、IMU、CAN、电机、导航或行走已验证。

## 2026-08-29 最终执行结果

- D1–D2：独立工程、安全入口、锁文件和 X5 同 fixture 的 S100 CPU 复现全部 PASS；官方离线套件及 U1–U3 均在无外设条件下完成。
- D3：10 个官方模型共 9 个 Nash-e BPU PASS、1 个明确锁定为 CPU fallback；必须 BPU 化的 encoder 与 policy 均 PASS。`policy_attn_enc` 在 6 个 HBM 候选均未同时满足冻结的数值/时延门后保留 CPUExecutionProvider，不虚报 BPU PASS。
- U1 补充闭环：项目自有时序占据/流/不确定性模型已生成 Nash-e HBM；128 组、3 输出 CPU/BPU A/B 的平均余弦为 `0.9998069839537895`，最差值 `0.9969569872868342`，BPU p50/p99 为 `0.806851/12.03518641 ms`，且第 0 个测试与原 X5 fixture 字节一致。
- D4：官方 YOLO26、ByteTrack、Depth Anything V2、PointNet 4/4 PASS。EdgeTAM、CenterPoint、StereoNet、LeRobot、ASAP、walk-these-ways、WBC-AGILE 均完成 commit/合同审计；缺系统依赖、机器人合同不匹配或仅适合作为训练参考的候选明确标为 BLOCKED/NOT_APPLICABLE，而非部署完成。
- D5：Nav2 20/20 合成目标 PASS；10,000 个策略步、40,000 个守卫子步与 8 类故障注入全部 PASS。30 分钟压力测试累计 `1,633,873` 次 BPU 调用、0 推理错误，最高 BPU 温度 `52.62 ℃`。
- D6：从全新目录复制锁定资产并重跑 D3 与 D4；D3 仍为 9 PASS + 1 CPU fallback，D4 仍为 4/4 PASS，重放前后资产哈希未变化，安全和残留进程检查 PASS。
- 最终聚合：`evidence/host/s100_offline_summary_latest.json` 的 `s100_offline_gate_complete` 与 `delivery_complete` 均为 `true`。完整交接见 `docs/S100_OFFLINE_DELIVERY.md`。

## 本阶段明确不做

- 不连接或驱动机器人、USB2CAN、执行器和传感器。
- 不启用/配置/发送 CANHAL、CAN2IPC、SocketCAN、串口或电机命令。
- 不烧录系统/MCU/固件，不改内核、udev、权限、零点、网络或启动服务。
- 不运行官方文档中会连接机械臂、电机、相机或真实机器人控制入口的命令。

USB2CAN/CANHAL 适配、无动力机器人闭环、单执行器和整机测试移到未来 `HW-*` 阶段，届时重新获得授权并执行机械、电气和急停门禁。

## 停止条件

地址不可达、SSH 凭据缺失、项目目录冲突、SKU/版本无法确认、磁盘不足、需要系统级安装/刷机/服务或网络变更、出现新 EULA/商业权重许可、OE/runtime 不匹配，或任何命令可能访问外设/控制通道时，立即停在当前门并报告。

## 官方一手参考

- [RDK S100/S100P 官方硬件说明](https://d-robotics.github.io/rdk_doc/en/rdk_s/Quick_start/hardware_introduction/rdk_s100/)
- [RDK S100/S100P 当前用户手册](https://d-robotics.github.io/rdk_s_doc/en/RDK/)
- [RDK S100 当前资源汇总](https://d-robotics.github.io/rdk_s_doc/en/Quick_start/download/)
- [D-Robotics RDK Model Zoo](https://github.com/D-Robotics/rdk_model_zoo/tree/rdk_s)
- [D-Robotics LeRobot BPU 工具](https://github.com/D-Robotics/rdk_LeRobot_tools)

网页和仓库内容只作为资料与后续来源锁，不自动构成运行其中命令的授权。

## 确认门

用户已明确回复 **“确认执行 S100 计划（D1–D6）”**，因此 D1–D6 的单板离线执行门已打开；该确认仅覆盖 S100 单主板离线范围，不扩大到机器人、外设、CAN、电机或真实控制。

即使确认后，若遇到系统级安装/刷机/网络或服务变更、新的 EULA/商业权重许可、磁盘扩容、需要终止用户进程，或任何真实设备访问，仍须停下并单独请求授权。
