# X5 隔离依赖与官方源码静态构建记录

执行日期：2026-08-28
结果：**PASS（仅依赖与静态构建就绪，不代表算法或硬件验证）**

## 执行边界

- 目标板：RDK X5，`aarch64`，内核 `6.1.83`，glibc `2.35`，Python `3.10.12`。
- 环境：ROS 2 Humble/TROS，`ROS_DOMAIN_ID=42`。
- 所有新增源码、归档、构建树、安装前缀、Python venv、日志和证据均位于 `/home/sunrise/workspaces/new_project/roboto_origin`。
- 未使用 `sudo`、`apt install`、系统级 `pip`、`rosdep install`，未写入 `/usr/local`、`/opt/ros` 或 `/opt/tros`。
- 未启动相机、雷达、定位、推理或电机节点；未访问执行器、未发送 CAN 帧，`can0` 始终保持 `DOWN`。

## 已锁定并构建的依赖

| 依赖 | 锁定版本/提交 | 项目内安装前缀 | 结果 |
| --- | --- | --- | --- |
| Sophus | `1.22.10` / `de0f8d3` | `deps/install` | PASS |
| pcl_msgs | `1.0.0` / `16c4174` | `deps/install/ros_pcl` | PASS |
| perception_pcl | Humble / `67a5c2b` | `deps/install/ros_pcl` | PASS |
| Livox-SDK2 | `v1.3.1` / `f5d9375` | `deps/install/livox_sdk2` | PASS |
| livox_ros_driver2 | `1.2.6` / `13eb05e` | `deps/install/livox_ros_driver2` | PASS |
| librealsense | `v2.58.1` / `bf27780` | `deps/install/librealsense` | PASS |
| realsense-ros | vendored / `926f37e` | `deps/install/realsense_ros` | PASS |
| Open3D | `0.17.0` 官方 CPython 3.10 ARM64 wheel | `deps/venv/open3d` | PASS |
| Pillow / SciPy | `11.3.0` / `1.15.3` ARM64 wheels | `deps/venv/open3d` | PASS |

精确 URL、完整提交、归档 SHA-256、wheel 和 Python 运行时锁文件见 `inventory/x5_dependency_lock.yaml`。

librealsense 使用 `FORCE_RSUSB_BACKEND=ON` 构建用户态后端，禁用示例、工具、测试、Python、CUDA、GLSL 和 rosbag2；本轮不安装 udev 规则、不运行 `ldconfig`、不修改内核。

Open3D 位于不继承系统 site-packages 的独立 venv。其运行时依赖由 ARM64/CPython 3.10 锁文件和哈希约束，`pip check`、导入及空点云探针均通过。为运行官方 PCD→PGM 工具，Pillow/SciPy 另由 `requirements/navigation-offline-x5-aarch64.lock` 与项目 bundle 哈希锁定并安装到同一 venv；没有借用 X5 的系统或用户 site-packages。项目环境只导出 `ROBOTO_OPEN3D_PYTHON`，不把该 venv 前置到 ROS 构建的 `PATH`。

## 官方源码静态构建

### 导航仓

锁定聚合提交 `d6ab991`，在隔离副本 `deps/src/roboparty_navigation-x5` 应用 `patches/roboparty-navigation-x5-build.patch`。补丁仅修复构建图：

- 使用现代 `Sophus::Sophus` 导入目标；
- 显式发现并链接 Boost Thread/System；
- 为 scan/IMU 内部库补齐 Ceres、glog 的传递关系、IKD-Tree 和 ScanAligner 链接关系。

下列 5 包构建、安装和 ROS 索引验证通过：

- `nlink_message`
- `serial`
- `nlink_parser_ros2`
- `nav2_localization_adapter`
- `robots_localization`

### 部署仓

锁定聚合提交 `a8a0f15`，在隔离副本 `deps/src/roboparty_deploy-x5` 应用 `patches/roboparty-deploy-x5-build.patch`。板端没有 `ccache`，补丁将 4 个包的无条件 `ccache` 设置改为“存在时才启用”，没有安装或模拟系统工具。

下列 4 包构建、安装和 ROS 索引验证通过：

- `camera`
- `roboparty_imu`
- `roboparty_motors`
- `roboparty_inference`

相机与推理包均使用官方仓内置、带哈希的 ONNX Runtime `1.21.0` ARM64 制品；推理包同时使用仓内置、带哈希的 yaml-cpp `0.9.0`。构建只生成二进制，没有加载模型或建立硬件连接。

## 可重复入口

板端执行前先进入项目目录：

```bash
cd /home/sunrise/workspaces/new_project/roboto_origin
source scripts/env_x5_deps.sh
```

依赖和官方源码的可重复入口分别为：

```bash
ROBOTO_BUILD_JOBS=2 bash scripts/board/build_x5_dependencies.sh all
ROBOTO_BUILD_JOBS=2 bash scripts/board/build_x5_official.sh all
bash scripts/board/capture_x5_dependency_evidence.sh
```

脚本先运行阶段 A 安全自检。`MAKEFLAGS` 和 `CMAKE_BUILD_PARALLEL_LEVEL` 同时固定实际编译并行度；官方源码只复制到隔离 staging 后打补丁，`src/official` 必须保持干净，否则脚本停止。

脚本支持单阶段增量复跑：

```bash
bash scripts/board/build_x5_dependencies.sh realsense_ros
bash scripts/board/build_x5_dependencies.sh navigation_python
bash scripts/board/build_x5_official.sh navigation
bash scripts/board/build_x5_official.sh deploy
```

## 验证证据

最新证据文件：`evidence/x5/dependencies/dependency_overlay_20260828T224108+0800.txt`

SHA-256：`910970aa66a03d9716283e374305ac4420a21b29ef367fcba14918803ecbf49b`

该证据确认：

- 17 个依赖与官方 ROS 包均可由项目 overlay 定位；
- Open3D `0.17.0` 导入、空点云探针、Pillow `11.3.0`、SciPy `1.15.3` 及 `pip check` 通过；
- 10 个核心 ELF/扩展的 `ldd` 均无 `not found`；
- deploy/navigation 两个官方检出工作树干净；
- `ROS_DOMAIN_ID=42`、`can0 DOWN`、4 个冻结服务 `inactive`；
- 没有相机、推理、定位或 RealSense 节点进程。

## 回退与剩余门禁

这些安装均为项目 overlay，不替换系统软件。逻辑回退只需不再 `source scripts/env_x5_deps.sh`。删除 `deps` 中的源码、构建树或安装前缀属于破坏性操作，不在本轮执行。

后续零输入 ONNX 运行和 PCD→PGM 离线转换见 `docs/X5_OFFLINE_PREWORK.md`。仍未验证 D435i、Livox、IMU、执行器、CAN 拓扑、真实输入数值正确性、实时频率、TF/定位/规划数据流或机器人动作。URDF/MJCF 限位及 torso 零偏冲突仍阻塞实机部署；静态构建 PASS 不解除 B0-01、A-04、B1/B2 或任何电气与急停门禁。
