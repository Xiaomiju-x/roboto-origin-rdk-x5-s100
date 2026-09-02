# X5 板端工作区

此目录的板端目标路径为 `/home/sunrise/workspaces/new_project/roboto_origin`。它与冻结 XRD 运行时隔离，当前只允许源码落盘、环境检查和不涉及设备输出的静态/离线工作。

每个 shell 先执行：

```bash
cd /home/sunrise/workspaces/new_project/roboto_origin
source scripts/env_x5.sh
```

硬边界：

- `ROS_DOMAIN_ID` 必须为 `42`。
- 项目可用端口从 `9104` 开始；`9100-9103` 已被现存程序占用。
- 不访问 `/dev/F407`，不修改 `/home/sunrise/ros2_ws` 或 `/home/sunrise/tools`。
- 不启动或恢复冻结 XRD 服务。
- 未通过实物安全门禁前，不把 CAN 接口设为 UP、不发送 CAN 帧、不读取执行器、不使能电机。
- `src/official` 保存锁定提交的官方源码；新实现后续放在 `src/project`，不得直接改官方副本。
- 输出只放在本目录的 `build/`、`install/`、`logs/`、`data/`、`models/`、`.cache/`。

只读检查：

```bash
bash scripts/verify_phase_a_env.sh
```

检查通过只说明环境和边界正确，不代表导航、视觉、强化学习、运动控制或机器人硬件已经复现。
