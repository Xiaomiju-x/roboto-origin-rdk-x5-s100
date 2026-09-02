# 验收矩阵

| 门 | 主机黄金 | X5 CPU | S100 CPU | S100 BPU | 负输入/故障 | 压力/重建 | 真实机器人 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 官方 O0–O6 | PASS | PASS | PASS | 9 PASS + 1 CPU fallback | PASS | D5/D6 PASS | 未验证 |
| U1 时序 BEV | PASS | PASS | PASS | PASS | PASS | PASS | 未验证 |
| U2 可信动作守卫 | PASS | PASS | PASS | N/A（CPU 安全逻辑） | PASS | 整链 PASS | 未验证 |
| U3 KISS-ICP | PASS | PASS | PASS | N/A | PASS | 洁净退出 PASS | 未验证 |
| 官方视觉 D4 | 来源/合同 PASS | 不作为本门结论 | 主机/板端准备 PASS | 4/4 smoke PASS | 边界检查 PASS | D6 重放 PASS | 未验证 |
| HW0–HW5 | N/A | N/A | N/A | N/A | N/A | N/A | NOT STARTED |

精确指标与声明边界见 [docs/RESULTS.md](../docs/RESULTS.md)。
