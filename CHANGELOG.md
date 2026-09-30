# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 的结构和语义化版本。

## [Unreleased]

### 2026-09-30 — X5底座 + S600外挂多模态开发者原型

- **新增源码与部署**：[apps/s600_coprocessor](apps/s600_coprocessor/README.md)，含真实三传感器采集、可观察网页、模型校验、独立运行入口及上游来源；[阶段说明](docs/S600_COPROCESSOR_DEMO.md)。源码提交为 [bd679ad](https://github.com/Xiaomiju-x/roboto-origin-rdk-x5-s100/commit/bd679ad6938c6d8e62a8f68024ae28d3949d1333)，[CI通过](https://github.com/Xiaomiju-x/roboto-origin-rdk-x5-s100/actions/runs/36696623882)。
- **S600 BPU**：YOLO11x-Seg实时分割、Qwen3-VL-2B场景问答、Whisper-medium通用中文转写；不再仅是CPU采集示例。四核固定图片回放217.64次/秒，单实例55.52次/秒；真实相机两分钟约19.2FPS，回放与实况分别报告。
- **三模块**：Astra Pro、STL-19P、M260C联合采集；真实5秒内存录音→Whisper→Qwen问答完成。原生深度和雷达坐标保留，不伪装成已配准目标测距。
- **RTX5090**：CUDA浮点验证及官方十输出ONNX导出，10/10张量检查通过；没有新增训练，不宣称现成HBM与该PT严格同源。
- **职责与基线纠正**：这是地瓜机器人公司领导牵头的正式项目，维护者以实习生身份负责相关算法；复用团队已验证X5官方行走/舞蹈底座，算法示例不发送运动指令，不以自写实验失败否定团队官方流程。
- **验证与隐私**：本地17项原工程测试、4项新增输出合同测试，板端8项测试及脱敏审计通过；不公开现场图像、原始音频、凭据、私网地址、权重或厂商二进制，各上游许可证独立。
- **已知边界**：RGB/深度及相机—雷达标定、现场语音准确率、实际足迹与几何导航、长时并发仍待验收；雷达近场遮挡导致规划拒绝，不清除真实障碍制造成功。这是可运行算法原型，不是自主导航或量产承诺。

### Added

- X5→S100→S600 YOLO 视觉 shadow 迁移：三板真实 BPU、统一归一化检测合同与 file-only `STOP_CANDIDATE`。
- S100/S600 YOLO11n 共用 runner、X5 原生基线 runner、跨板比较工具和脱敏机器摘要。

### Known limitations

- S100/S600 功能事件一致，但严格分数差门 0.05 未通过（最大差 0.05157）。
- X5 modified YOLO11 与旧跟踪器的零面积框路线保留为失败，不作为基线。

### Earlier plans (historical)

- 以下为早期独立硬件开发计划；现阶段复用团队X5已验证底座，优先S600标定、融合与可靠性验证，不要求重新执行整套旧分级。
- HW0 无动力物料、电气、线束、急停与身份核对。
- HW1 X5 只读通信合同与 shadow/file-sink 对照。

## [0.1.0] - 2026-09-02

### Added

- X5 官方 O0–O6 离线复现脚本、配置、补丁与验收文档。
- X5 U1 时序占据/流、U2 可信动作守卫、U3 KISS-ICP 升级。
- S100 D0–D6 CPU/BPU、官方视觉、故障注入、压力与洁净重建工具。
- 脱敏机器摘要、结果边界、复现指南、安全门禁与 HW0–HW5 路线图。
- Apache-2.0 许可、贡献/安全政策、CI 与公共发布审计器。
