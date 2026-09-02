# 文档索引

## 当前权威入口

- [结果与声明边界](RESULTS.md)
- [复现指南](REPRODUCIBILITY.md)
- [真实机器人安全边界](SAFETY.md)
- [HW0–HW5 路线图](ROADMAP.md)
- [公共发布策略](PUBLIC_RELEASE.md)
- [当前状态](../delivery/STATUS.md)
- [S100 离线交付](S100_OFFLINE_DELIVERY.md)

## 阶段与设计记录

其余文档记录项目从静态审计、X5 官方离线基线、X5 升级到 S100 D0–D6 的推进过程。阶段文档中的“下一步”与“门已关闭/未关闭”描述反映其生成时点，不覆盖当前权威状态；阅读时请与上面的 `RESULTS.md` 和 `delivery/STATUS.md` 对照。

## 文档约定

- `PASS` 必须指向具体门和机器证据。
- 失败和 fallback 是一等结果，不用降低阈值或模糊命名掩盖。
- 文档不包含私网端点、凭据、个人绝对路径或不可再分发资产。
- 第三方来源、commit 和许可证以 `inventory/` 与 `config/` 为准。
