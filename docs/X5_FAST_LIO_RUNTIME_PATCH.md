# X5 FAST-LIO2 离线运行补丁记录

记录日期：2026-08-29。本记录只涉及项目隔离副本 `deps/src/roboparty_navigation-x5`，本地 `upstream/roboparty_navigation` 与板端 `src/official/roboparty_navigation` 始终保持锁定提交和干净工作树。

## 测试边界

节点在 `ROS_DOMAIN_ID=42` 下接收项目生成的确定性 Ouster `PointCloud2` 和 200 Hz IMU；未启动雷达/IMU 驱动，未连接机器人或外设，`can0` 始终 `DOWN`，四个冻结服务始终 inactive。每帧点云 4,060 点，验收要求包含：输入实际发布、有限 odometry、registered cloud、path、静止位移有界、SIGINT 后退出码 0、无残留进程。

## 发现与证据链

1. `evidence/x5/localization_synthetic/2026-08-29T00-45-56+08-00/`：算法数据链通过，但官方后台线程结束时显式调用 `ikdtree.~KD_TREE()`，对象随后还会自动析构，退出阶段发生段错误。
2. `evidence/x5/localization_synthetic/2026-08-29T00-57-01+08-00/`：移除显式析构后不再立即崩溃，但节点覆盖了 rclcpp 的 SIGINT 处理器，`rclcpp::spin()` 不退出；10 秒宽限后被自有脚本终止，退出码 137。
3. `evidence/x5/localization_synthetic/2026-08-29T01-10-54+08-00/`：恢复 rclcpp 信号处理并把后台线程改为可 join 后，线程能够结束，但退出码仍为 139。
4. `evidence/x5/localization_shutdown_debug/gdb.log`：GDB 将退出段错误定位到 `DomainParticipant::set_listener → destroy_participant → rmw_destroy_node`。官方 publisher、subscription、timer 与 TF broadcaster 是全局智能指针；Node/FastDDS participant 先析构后，这些全局 ROS 实体在进程退出时再次释放，形成生命周期逆序。
5. `evidence/x5/localization_synthetic/2026-08-29T01-38-00+08-00/`：派生 Node 析构期提前释放 ROS 实体后，算法输出仍通过，但进程退出时发生 `double free or corruption (!prev)`，退出码 134。
6. `evidence/x5/localization_shutdown_debug/2026-08-29T01-38-59+08-00/gdb.log`：第二轮 GDB 将 `SIGABRT` 定位到 `libimu_processor.so` 的 `std::deque<common_lib::IMU>::~deque()`。`common_lib.h` 直接定义了多组非平凡全局对象，它们被编入主可执行文件及多个共享库，动态符号抢占使同一对象重复注册析构。

## 审计补丁

- `patches/roboparty-navigation-x5-runtime.patch`：删除手工调用的 `KD_TREE` 析构函数，保留 C++ 自动生命周期。
- `patches/roboparty-navigation-x5-thread-lifecycle.patch`：不再覆盖 rclcpp SIGINT；将 detached 计算线程改为成员线程，在派生 Node 析构时置退出标志、通知并 join；`main` 在返回前显式释放 Node。
- `patches/roboparty-navigation-x5-ros-entity-lifecycle.patch`：在派生 Node 析构函数内、基类 Node 与 FastDDS participant 仍存活时，按订阅/定时器/TF/发布器顺序释放所有全局 ROS 实体。
- `patches/roboparty-navigation-x5-common-globals.patch`：将 `common_lib.h` 中共享的非平凡全局变量改为 C++17 `inline` 变量，ELF 动态符号由重复的 `B` 收敛为带 guard 的 GNU unique `u`，确保进程内只初始化和析构一次，同时保持主节点与 `imu_processor` 共享 ZUPT 缓冲。

构建脚本先校验官方工作树干净，再把四个补丁按固定顺序应用到隔离副本。线程补丁使用四个独立标记完成重启后的幂等核验，避免后续补丁扩展同一析构区块时让整块反向校验误报。

## 当前结果

`evidence/x5/localization_synthetic/2026-08-29T02-00-12+08-00/result.json` 最终 PASS：输入 68 帧点云、1,943 帧 IMU，输出 65 帧有限 odometry、30 帧 registered cloud 和 6 帧 path，静止最大位移 0.003111288 m。`SIGINT` 后 `shutdown.graceful: true`、`shutdown.exit_code: 0`，随后 `pgrep` 确认无残留定位进程。本项和 O6 现为 PASS。
