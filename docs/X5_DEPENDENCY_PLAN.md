# X5 依赖补齐与隔离构建方案

状态：**用户已确认；项目内隔离构建与无设备验证已于 2026-08-28 执行并通过。**

执行前的软件源缓存对 `ros-humble-pcl-ros`、`ros-humble-pcl-conversions`、`librealsense2-dev`、`ros-humble-librealsense2`、`libsophus-dev` 和 `python3-open3d` 均没有候选版本。该结果只代表当时缓存快照；本项目未执行 `apt update`，而是采用锁定来源与 SHA-256 的项目内制品。

## 目标与边界

- 所有源码、下载缓存、构建树、安装前缀、Python 环境和日志均放在 `/home/sunrise/workspaces/new_project/roboto_origin/deps`。
- 不修改 `/opt/ros`、`/opt/tros`、`/usr/local`、冻结 XRD 资产或设备规则。
- 不使用 `sudo apt install`、系统级 `pip`、`rosdep install -y` 或无锁定版本的拉取。
- 依赖就绪只允许进入“可构建”状态，不自动启动节点、连接传感器、打开 CAN 或运行策略。

## 已锁定依赖组

| 组 | 原缺口 | 实际形式 | 结果 |
| --- | --- | --- | --- |
| 导航点云 | `pcl_ros`、`pcl_conversions` | 锁定 Humble 源码 overlay | PASS；X5 PCL 1.12/ROS Humble 编译通过 |
| Livox | `livox_ros_driver2` 及 SDK | SDK `v1.3.1`、driver `1.2.6` 项目内安装 | PASS；未连接雷达 |
| 定位数学库 | Sophus | `1.22.10` 项目内 CMake 安装 | PASS；官方定位包链接通过 |
| 深度相机 | librealsense2 | `2.58.1` RSUSB 用户态构建及 vendored ROS wrapper | PASS；未改 udev/内核、未连接相机 |
| 地图工具 | Open3D | 官方 `0.17.0` CPython 3.10 ARM64 wheel 与哈希锁定 venv | PASS；导入、空点云与 `pip check` 通过 |

## 实际执行阶段

1. **锁定与归档**：为每个依赖记录来源 URL、精确 commit/tag、许可证、SHA-256 和递归子模块；先在主机下载审查，再传入 X5 项目目录。
2. **工具链探针**：只做 CMake configure/编译器 ABI 探针，输出到 `deps/logs`。任何发现需要系统目录或内核/udev 修改的步骤立即停止并单独审批。
3. **隔离构建**：依次构建 Sophus、ROS PCL overlay、Livox SDK/driver、librealsense 用户态库和 Open3D 环境，统一安装到 `deps/install` 或 `deps/venv`。
4. **环境入口**：仅在项目自有 `env_x5.sh` 中追加可回退的 `CMAKE_PREFIX_PATH`、`AMENT_PREFIX_PATH`、`LD_LIBRARY_PATH` 与 venv 入口；修改前保存文件哈希和副本。
5. **无设备验证**：验证包发现、动态链接和模型 synthetic input；仍不启动硬件节点。每组依赖单独 PASS/FAIL，失败不影响已通过组。
6. **离线源码构建**：先 build deploy/navigation 指定包，不执行官方一键启动脚本。保留 colcon command、编译器版本、日志和安装清单。

## 回退

隔离方案不替换系统包。回退只需恢复项目环境入口并停用 `deps/install`/`deps/venv`；实际删除构建目录属于独立破坏性操作，届时再确认。若某依赖只能通过系统级安装、udev 或内核模块完成，应拆成新审批项，不得混入本方案静默执行。

## 执行记录

用户已明确允许继续，依赖、官方 navigation/deploy 指定包及静态验证均通过。完整版本、命令、补丁、结果与证据见 `docs/X5_DEPENDENCY_BUILD.md`。该 PASS 仍不解除关节契约、实物电气、急停、支撑、CAN 拓扑和执行器身份门禁。
