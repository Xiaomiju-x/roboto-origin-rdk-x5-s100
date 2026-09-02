# 验收矩阵

| 门 | 主机黄金 | X5 | S100 | S600 | 负输入/故障 | 压力/重建 | 真实机器人 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 官方 O0–O6 | PASS | CPU PASS | CPU PASS / BPU 9 PASS + 1 fallback | 未纳入 | PASS | D5/D6 PASS | 未验证 |
| U1 时序 BEV | PASS | CPU PASS | CPU/BPU PASS | 未纳入 | PASS | PASS | 未验证 |
| U2 可信动作守卫 | PASS | CPU PASS | CPU PASS | 未纳入 | PASS | 整链 PASS | 未验证 |
| U3 KISS-ICP | PASS | CPU PASS | CPU PASS | 未纳入 | PASS | 洁净退出 PASS | 未验证 |
| 官方视觉 D4 | 来源/合同 PASS | 不作为本门结论 | 4/4 BPU smoke PASS | 未纳入 | 边界检查 PASS | D6 重放 PASS | 未验证 |
| YOLO shadow 迁移 | 合同/归一化 PASS | YOLO26+ByteTrack 3/3 PASS | YOLO11 BPU 10/10 PASS | YOLO11 BPU 10/10 PASS | 文件输出安全门 PASS；严格分数对齐 FAIL | 短程确定性 PASS；未做本门长稳 | 未验证 |
| HW0–HW5 | N/A | N/A | N/A | N/A | N/A | N/A | NOT STARTED |

精确指标与声明边界见 [docs/RESULTS.md](../docs/RESULTS.md)。
