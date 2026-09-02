# B0 静态就绪度

首轮只解析锁定源码、ONNX protobuf 元数据与 X5 软件清单；后续经用户确认，已在项目目录内补齐锁定依赖并完成官方 deploy/navigation 静态构建。此后又完成 10 个模型的零输入运行时探针和 PCD→PGM 离线转换，但仍未启动 ROS 节点，`can0` 保持 `DOWN`。

## ONNX 接口：PASS

`scripts/host/audit_onnx_contract.py` 在不依赖 ONNX Runtime 的情况下盘点了 10 个模型文件。9 个策略模型的静态输入维数与各自 YAML 观测配置一致，输出均为 23 维 `float32` 关节动作；深度编码器为 `[1,8,18,32] -> [1,128]`，与 480×270 深度图缩放至 64×36、左右各裁 16、上裁 18、8 帧堆叠的配置一致。

静态审计只证明文件清单、张量形状与配置契约一致。后续 X5 CPU ONNX Runtime `1.21.0` 零输入探针已实现 10/10 模型 PASS，但仍不证明真实数据数值正确性、BPU 兼容性、帧率或实时截止期。运行记录见 `docs/X5_OFFLINE_PREWORK.md`。

## X5 依赖与静态构建闭环：PASS

初始静态清单确认的以下 6 项构建前置现已全部在项目目录内补齐：

- `librealsense2` 开发包；
- `pcl_ros` 与 `pcl_conversions`；
- `livox_ros_driver2`；
- Sophus CMake 包；
- Open3D Python 模块。

官方导航 5 包和部署 4 包均在 X5 上完成静态构建、安装与 ROS 包索引验证。10 个核心 ELF/扩展的动态依赖均已解析，Open3D venv 的导入、空点云探针和 `pip check` 通过；离线导航所需 Pillow/SciPy 也已在同一项目 venv 中验证。完整记录见 `docs/X5_DEPENDENCY_BUILD.md`，最新机器证据见 `evidence/x5/dependencies/dependency_overlay_20260828T224108+0800.txt`。

`serial/package.xml` 仍声明 `catkin`，但其 export 与 CMakeLists 已使用 `ament_cmake`，保留为源码清单警告。板端没有 `ccache`，隔离补丁改为存在时才启用；没有安装系统包。该 PASS 只说明构建前置和静态二进制闭环，不表示相机、雷达、定位、推理、控制或机器人能力已运行。

## 单一关节契约候选：NON-DEPLOYABLE

`config/contracts/rpo_x5_candidate.json` 汇总了 23 关节顺序、官方 motor ID/总线分组、方向、参考增益、三种零偏来源，以及规范 URDF/MJCF 和 GMR URDF/MJCF 的限位。它记录 15 个公开限位冲突与 1 个 torso 零偏冲突，并给出仅用于差异分析的静态交集。

该候选明确设置 `deployable: false`，不得加载到驱动或策略节点。机械限位、硬停、执行器型号、固件保护、实际零位、四路 CAN 拓扑、急停与支撑未用实物证据确认前，不可把静态交集称为安全限位。
