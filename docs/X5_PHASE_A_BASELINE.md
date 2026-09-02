# X5 阶段 A 实测基线

采集日期：2026-08-28。范围仅限获授权的 HDMI 切换、远程服务恢复和只读盘点；未打开 CAN、未访问 `/dev/F407`、未启动机器人代码或执行器。

## 已验证

| 项目 | 实测结果 |
| --- | --- |
| 连接 | SSH 可达且现有密钥登录成功；公共快照不披露端点 |
| 系统 | X5 RDK，board_id 302 / rev 1_B，8 GB LPDDR4；Ubuntu 22.04.5，Linux 6.1.83，arm64 |
| 隔离 | `/home/sunrise/workspaces/new_project/env.sh` 已加载，`ROS_DOMAIN_ID=42` |
| 冻结状态 | SSH/VNC 启用；`embodied_brain`、`cockpit_bridge`、`eb_sensor_watchdog`、`xrd-v6-8890` 均为 `disabled/inactive` |
| 显示 | 移除 4.3 英寸 DSI overlay，Xorg 忽略 DSI、不忽略 HDMI；重启后 `HDMI-1` 为主输出，`1024x600@59.82Hz` |
| 远程桌面 | `x11vnc.service` 为 `enabled/active`，Windows 到 TCP 5900 可达 |
| ROS | `/opt/ros/humble` 与 `/opt/tros/humble` 存在；source TROS 后 ROS 2 Humble 可用，`rclcpp`、`nav2_bringup` 可解析 |
| CAN | 仅枚举 `can0`，m_can 位于 spi5.0；保持 `DOWN/STOPPED`，RX/TX 均为 0 |
| USB | 仅看到 Genesys USB 2.0/3.1 Hub；未看到三合一板、USB-CAN、相机或雷达身份 |
| 端口 | `9100-9103` 已被现存服务占用；本项目不得复用或终止这些进程 |
| 官方源码 | 五个锁定仓库共约 659 MB 已复制到 `src/official`；提交、递归子模块和干净工作树全部通过板端自检 |
| 板端入口 | `source scripts/env_x5.sh` 显式加载 ROS/TROS、强制 Domain 42，并声明端口范围 `9104-9199` |

## HDMI 改动与回退

板端备份目录：

`/home/sunrise/workspaces/new_project/roboto_origin/evidence/x5/display/20260828T171118+0800`

本次改动与 X5 自带 `srpi-config` 的 HDMI 选择逻辑一致：

- 从 `/boot/config.txt` 删除 `dtoverlay=dsi-waveshare-panel-overlay-4_3_inch`。
- 将 `xorg_hdmi_ignore.conf` 改名为停用状态 `.disable`。
- 将 `xorg_dsi_ignore.conf.disable` 改名为生效状态。

回退只作为说明，未经用户再次授权不执行：恢复备份的 `/boot/config.txt`，把两个 Xorg ignore 文件名恢复到切换前状态，然后重启。恢复前应先确认目标文件和备份哈希一致。

## 证据

- `evidence/x5/SHA256SUMS`：板端生成的九个证据文件 SHA-256 清单；复制到 Windows 后已逐项复核一致。
- `evidence/x5/display/20260828T171118+0800/`：切换前配置、第一次启动快照和回退原件。
- `evidence/x5/phase_a/board_inventory_20260828.txt`：系统、网络、接口和冻结状态原始输出。
- `evidence/x5/phase_a/ros_packages_ports_20260828.txt`：ROS/TROS、端口、最终 HDMI/VNC 状态。
- `evidence/x5/phase_a/source_env_verification_20260828.txt`：官方源码提交/子模块、ROS 环境、冻结服务、CAN 与 HDMI 的最终板端自检。

`after_state.txt` 是重启后显示栈尚未完成初始化时的早期快照，包含临时的 2000 年系统时间和 HDMI `unknown`；最终状态以 `ros_packages_ports_20260828.txt` 为准。

## 当前阻塞项

阶段 A 的实物部分仍为 `BLOCKED`：缺少电池/供电标签、极性/保险/接地、CAN_H/CAN_L/GND 和端接、三合一板版本、执行器型号与节点 ID、机械支撑及物理急停证据。因此不得进入执行器只读通信，更不得使能或运动。
