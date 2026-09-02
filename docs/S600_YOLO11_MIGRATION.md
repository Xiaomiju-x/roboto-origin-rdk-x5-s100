# YOLO 视觉影子链：X5 → S100 → S600

更新时间：2026-09-02

## 目标与边界

本增量从 RDK 前沿算法生态实验室复用 S600 上已经验证的官方 YOLO11n Nash-p BPU 模型合同，把它适配为萝博头可消费、但不会控制机器人的视觉影子链：

`本地图像/视频 → 板端 BPU 检测 → CPU 后处理/跟踪 → 归一化检测 → STOP_CANDIDATE/CLEAR JSON 文件`

它不打开相机，不访问 CAN、串口、电机或控制器，不发布 ROS 运动话题，也不生成可直接执行的速度、转矩或位置命令。`STOP_CANDIDATE` 只是供未来 HW3 对照的文件事件，不是急停实现。

## 为什么没有把一个二进制复制到三块板

三块板的 BPU 架构与制品格式不同：

| 平台 | 角色 | 模型/制品 | 本次结果 |
| --- | --- | --- | --- |
| X5 / Bayes-e | 萝博头原生安全基线 | 已验证 YOLO26s `.bin` + ByteTrack | `X5_YOLO26_BYTETRACK_SHADOW_FILE_PASS` |
| S100 / Nash-e | 第一块 S 系列迁移目标 | YOLO11n detect `.hbm` | `S100_YOLO11_BPU_SHADOW_FILE_PASS` |
| S600 / Nash-p | 算法扩展目标 | YOLO11n detect `.hbm` | `S600_YOLO11_BPU_SHADOW_FILE_PASS` |

X5 `.bin`、S100 Nash-e `.hbm` 和 S600 Nash-p `.hbm` 没有跨板复用。真正复用的是输出协议、DFL/NMS 后处理、归一化检测合同和 file-only shadow guard。

## 实际迁移过程

1. **X5 先行。** 用已冻结的 YOLO26s+ByteTrack 基线重跑 3 次 30 帧离线视频，三份 CSV 逐字节一致：78 条轨迹、4 个 ID；第一帧 3 个目标进入统一 guard，输出 `STOP_CANDIDATE`。
2. **记录不可直接复用分支。** X5 板载 `yolo11m_detect_*_modified.bin` 能给出类别分数，但旧跟踪器组合把所有框坐标压成零面积，因此该路线失败，没有记为 PASS。
3. **S100 适配。** 将 S 系列双 NV12 输入、量化输出反量化、YOLO11 DFL16 解码、分类别 NMS 和坐标回映射封装为单一 runner。首次内联调度参数被该板 Python 绑定拒绝；只改为预先调用 `set_scheduling_params()` 后通过。
4. **S600 复用。** 完全复用 S100 runner 和相同测试图，只替换为 S600 身份、Nash-p 模型路径与冻结哈希；无需 RTX 5090 重新训练或编译。
5. **跨板比较。** S100/S600 都输出 4 个 person 和 1 个 bus，最差匹配框 IoU 0.9655，最终事件相同；严格分数差门 0.05 未通过（最大 0.05157），所以只声明功能/安全事件迁移 PASS，不声明跨板数值等价。

## 机器结果

| 指标 | X5 | S100 | S600 |
| --- | ---: | ---: | ---: |
| 实际 BPU | Bayes-e | Nash-e | Nash-p |
| 冻结运行数 | 3×30 帧 | 10 | 10 |
| 任务结果 | 78 轨迹行 / 4 ID | 5 检测 | 5 检测 |
| 推理/进程 P50 | 3.427 s / 30 帧 | 3.402 ms | 1.594 ms |
| shadow 事件 | STOP_CANDIDATE | STOP_CANDIDATE | STOP_CANDIDATE |
| 运动输出 | 无 | 无 | 无 |
| 退出后 BPU | 空闲 | 空闲 | 空闲 |

X5 指标包含视频解码、BPU 检测和 CPU 跟踪的整进程；S100/S600 指标只统计 BPU `run()`，不能横向当作同口径吞吐比较。

## 复现

项目自有的公共代码：

- `src/roboto_upgrade/yolo_shadow.py`：跨板归一化检测与 shadow 决策；
- `scripts/board/run_x5_yolo_shadow.py`：X5 原生基线 runner；
- `scripts/board/run_s_series_yolo11_shadow.py`：S100/S600 共用 runner；
- `scripts/host/compare_yolo_shadow_results.py`：跨板结果比较；
- `config/platform/yolo_shadow_migration.yaml`：制品哈希、角色和声明边界。

模型二进制和测试图没有再分发。复现者需从各自有权使用的官方 RDK Model Zoo/板载资源取得，随后将路径显式传给 runner。机器摘要位于 `evidence/summaries/*yolo*`。

## 尚未通过

- X5 modified YOLO11 跟踪组合的框几何门；
- S100/S600 严格分数差门；
- 真实萝博头相机、标定、时钟、丢帧和环境精度；
- 真实急停、控制链、导航闭环和运动安全。

因此本增量只关闭“三板离线视觉 shadow 迁移”门，不改变 HW0–HW5 的 `NOT STARTED` 状态。
