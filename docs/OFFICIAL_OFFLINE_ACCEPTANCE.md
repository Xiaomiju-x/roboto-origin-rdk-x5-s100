# 官方算法离线复现验收

本阶段只验证笔记本训练/仿真、模型导出和 X5 单板离线推理，不连接机器人、CAN、电机、相机、雷达、IMU 或其他外设。任务矩阵的机器可读版本是 `config/official_training_matrix.yaml`。

“完整复现”按 O0–O6 七个门禁逐任务判定：源码与契约、环境导入、仿真 reset/step、短训练与 checkpoint、模型导出、有限时长 MuJoCo sim2sim、X5 离线部署。任何一项未执行或失败，都不能把该任务写成 PASS。

官方默认训练规模为 4096 或 8192 个并行环境；本机只有约 16 GB 内存和 RTX 4050 6 GB 显存。先从 1 个环境运行，依据实测逐步增大。这里验收的是训练—保存—导出—回放闭环和部署兼容性，不以这台笔记本在空窗期内重现官方长周期收敛曲线作为虚假的完成条件。

官方源码保持只读。为避免官方脚本的无限循环、交互窗口和设备依赖，有限步数、固定随机种子、指标采集和 ONNX 适配只写在项目自有包装层；原命令、原配置和差异会同时留证。

在全部官方任务通过前，不进入 XRD/联网前沿升级；升级完成并在 X5 离线验收通过前，不要求 S100 上电。

## 当前门禁状态（2026-08-29）

| 门禁 | 状态 | 当前证据与缺口 |
| --- | --- | --- |
| O0 源码与契约 | PASS | 10 个注册任务、8 个训练任务、10 个部署模型及源码提交均已机器审计，见 `evidence/host/training/official_task_registry_20260829T0042+0800.json`。 |
| O1 环境导入 | PASS | Python 3.11、Isaac Sim 5.1.0、锁定的 Isaac Lab `b0542fe2…`、CUDA PyTorch、RoboLab 与项目 RSL-RL 已实际启动；8 个任务均完成配置解析和环境创建。完整环境锁见 `inventory/host_training_lock.yaml`。 |
| O2 仿真 reset/step | PASS | Flat、Rough、AMP、AttnEnc、Interrupt、BeyondMimic、Getup-Mimic、Parkour 均以 1 个环境完成训练采样，并由各自官方 play 路由完成 2 个策略步，日志含 `completed_steps=2`。 |
| O3 短训练/checkpoint | PASS | 8/8 任务以 `--num_envs 1 --max_iterations 1 --logger tensorboard` 完成真实学习更新；共生成 8 个非空 checkpoint 和 16 份解析后的 env/agent YAML。 |
| O4 checkpoint 再导出 | PASS | 8/8 checkpoint 均经对应官方 play/export 路由重新加载。七项输出 JIT+ONNX，Parkour 输出 depth encoder+actor 两个 ONNX，共 16 个非空导出工件；7 个 TorchScript 与 9 个 ONNX 的结构/有限值推理复核均 PASS。 |
| O5 有限 MuJoCo sim2sim | PASS | Flat、Rough、AMP、AttnEnc、Interrupt、BeyondMimic（wave/dance0/dance1）、Getup、Parkour（plane/stairs，含 CUDA）均为有限值并生成回放证据，见 `evidence/host/mujoco/`。 |
| O6 X5 离线部署 | PASS | 10 个模型非零输入运行、主机/X5 数值对照、深度视觉、PCD 地图与 Nav2 路径规划已通过；FAST-LIO2 合成点云/IMU 回归在 `evidence/x5/localization_synthetic/2026-08-29T02-00-12+08-00/result.json` 中以退出码 0 优雅结束且无残留进程。 |

完整八任务聚合证据为 `evidence/host/isaac/20260829T075941+0800/aggregate.json`；checkpoint、YAML、TorchScript、ONNX 与有界脚本差异复核为同目录的 `artifact_verification.json`；9 个新导出 ONNX 的 CPU/CUDA Provider 对照为 `export_backend_probe.json`。总汇总 `evidence/host/official_offline_summary_latest.json` 已给出 O0–O6 全 PASS、`upgrade_gate_open=true`。

用户已明确确认 NVIDIA 许可门禁。运行包装仍默认拒绝启动，只有项目确认变量和命令行确认开关同时存在才执行；厂商要求的 `OMNI_KIT_ACCEPT_EULA=YES` 仅传给当次子进程，未写入系统环境。

## 有界 headless 回放说明

官方 play 脚本只在 `--video` 模式按视频长度自动退出。在当前 Windows/RTX 环境中，渲染模式在策略加载前触发 Isaac Sim 5.1 RTX/Replicator 原生访问冲突，证据保留在 `evidence/host/isaac/20260829T075516+0800/flat/play_export.log`；这不是训练或策略错误。

验收器因此从每个锁定官方 play 脚本生成证据目录内的临时副本，只新增 `--max_steps` 参数、步数计数和完成标记，不启用渲染，也不修改官方 checkout。`artifact_verification.json` 已逐行确认副本无删除行且只有预期新增，8 个官方源码仓库状态保持 clean。

## 阶段门禁

官方 X5 离线基线已完成，允许进入外部只读 XRD 开源快照与联网前沿算法的 X5 升级/A-B 阶段。此处记录的是当时的阶段门；当前总状态以仓库根 README 与 `docs/RESULTS.md` 为准。
