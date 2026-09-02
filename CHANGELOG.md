# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 的结构和语义化版本。

## [Unreleased]

### Added

- X5→S100→S600 YOLO 视觉 shadow 迁移：三板真实 BPU、统一归一化检测合同与 file-only `STOP_CANDIDATE`。
- S100/S600 YOLO11n 共用 runner、X5 原生基线 runner、跨板比较工具和脱敏机器摘要。

### Known limitations

- S100/S600 功能事件一致，但严格分数差门 0.05 未通过（最大差 0.05157）。
- X5 modified YOLO11 与旧跟踪器的零面积框路线保留为失败，不作为基线。

### Planned

- HW0 无动力物料、电气、线束、急停与身份核对。
- HW1 X5 只读通信合同与 shadow/file-sink 对照。

## [0.1.0] - 2026-09-02

### Added

- X5 官方 O0–O6 离线复现脚本、配置、补丁与验收文档。
- X5 U1 时序占据/流、U2 可信动作守卫、U3 KISS-ICP 升级。
- S100 D0–D6 CPU/BPU、官方视觉、故障注入、压力与洁净重建工具。
- 脱敏机器摘要、结果边界、复现指南、安全门禁与 HW0–HW5 路线图。
- Apache-2.0 许可、贡献/安全政策、CI 与公共发布审计器。
