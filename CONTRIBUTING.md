# 贡献指南

感谢参与。这个项目优先保证状态诚实、硬件安全和可复现性。

## 提交问题

请说明：

- 目标板卡、系统、ROS/UCP/HBRT 或相关运行时版本；
- 精确脚本、命令、输入合同和预期结果；
- 实际结果、最小脱敏日志与复现频率；
- 是否涉及真实传感器、CAN、串口、电机或执行器；
- 已尝试的恢复步骤。

不要上传密码、Token、私钥、私网地址、MAC、Wi-Fi 信息、设备序列号、个人绝对路径或未授权的模型/数据。

## 提交 Pull Request

1. 从 `main` 创建聚焦的分支。
2. 一个 PR 只解决一个可验证问题。
3. 更新或新增测试、状态文档和必要的来源/依赖锁。
4. 保留失败证据；不要为了通过而降低既定阈值。
5. 运行：

   ```bash
   python -m pytest
   python -m compileall -q src probes scripts tests
   python scripts/ci/public_release_audit.py
   ```

6. 在 PR 中明确：已通过门、未通过门、实际改动、风险、回退与许可证影响。

涉及真实硬件的 PR 不得把可直接触发运动的默认行为合入。设备输出必须默认禁用或指向 shadow/file sink，并提供显式、安全、分级的启用门。

## 提交与版本

推荐使用清晰的祈使句提交说明，例如：

```text
Add S100 negative-input gate for temporal BPU runner
```

行为或证据合同变化写入 `CHANGELOG.md`。发布采用语义化版本；硬件/模型状态变化必须同时更新 `docs/RESULTS.md` 与 `docs/ROADMAP.md`。

## 许可证

提交即表示你有权贡献相应内容，并同意项目自有贡献按 Apache-2.0 发布。第三方代码、补丁、模型、数据或生成物必须说明来源和许可证；无法确认再分发权利的内容不要提交。
