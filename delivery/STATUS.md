# 当前状态

更新时间：2026-09-02

## 结论

`X5_OFFLINE_O0-O6_PASS / X5_UPGRADE_U1-U3_PASS / S100_D0-D6_OFFLINE_PASS / PHYSICAL_ROBOT_HW0-HW5_NOT_STARTED`

X5 与 S100 的单板离线算法阶段已经关闭。该结论只覆盖冻结或合成 fixture 下的主机/CPU/BPU、负输入、压力和洁净重建门，不覆盖真实机器人、传感器、CAN、串口、电机、执行器、导航、行走或急停行为。

## 已完成

- X5 官方导航、视觉、强化学习、运动策略等离线基线 O0–O6。
- X5 U1 时序占据/流/不确定性、U2 可信动作守卫、U3 KISS-ICP 升级。
- S100 D0–D6：只读盘点、隔离工程、同 fixture CPU 复现、Nash-e BPU A/B、官方视觉 smoke、前沿候选审计、Nav2 多目标、故障注入、30 分钟压力和洁净重建。
- S100 官方模型结果：9 BPU PASS、1 明确 CPU fallback、0 FAIL。
- S100 U1 时序 BEV Nash-e BPU A/B PASS。
- 公共代码、配置、补丁、复现合同、安全门、脱敏摘要和 CI 发布面建立。

## 未执行

- 机器人 HW0 无动力物料、电气、线束、急停和身份核对。
- X5 HW1 真实通信只读闭环。
- 任何带动力、使能、运动或转矩命令。
- 真实相机、雷达、IMU、CAN、串口、电机或执行器访问。
- 真实导航、定位、姿态、行走或整机闭环。
- S100 真实机器人控制适配。

## 证据入口

- [结果总览](../docs/RESULTS.md)
- [S100 离线交付](../docs/S100_OFFLINE_DELIVERY.md)
- [X5 官方离线验收](../docs/OFFICIAL_OFFLINE_ACCEPTANCE.md)
- [X5 升级验收](../docs/X5_UPGRADE_ACCEPTANCE.md)
- [脱敏机器摘要](../evidence/summaries)

## 下一门

下一阶段是 HW0，必须保持机器人无动力，建立 BOM、电气/线束、执行器身份、急停/断电和 GO/NO-GO 证据。HW0 未通过不得进入 HW1；HW0/HW1 未通过不得给执行器上动力。
