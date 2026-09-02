# 复现指南

## 复现原则

1. 固定上游 URL、commit、模型哈希、依赖版本与输入 fixture。
2. 每次只关闭一个验收门，失败记录不覆盖。
3. 主机、X5 CPU、S100 CPU、S100 BPU、压力与洁净重建分别记账。
4. 板端运行前后都检查 Domain、设备状态、进程残留、资源和输出哈希。
5. 真实硬件接入前，所有控制类输出保持 shadow 或 file sink。

## 主机环境

最小静态检查只需要 Python 3.10+：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pytest
python -m compileall -q src probes scripts tests
python scripts/ci/public_release_audit.py
```

模型类任务按需安装：

```bash
python -m pip install -e ".[model,onnx]"
```

ROS 2、Nav2、OpenExplorer、HBRT/UCP、Isaac Sim/Lab、MuJoCo 等环境不由 PyPI extras 代替。请使用目标版本的官方安装方式，并核对 `inventory/` 中的锁定信息。

## 上游来源

本仓库不包含上游完整源码。按 `inventory/source_manifest.yaml`、`inventory/x5_dependency_lock.yaml`、`inventory/upgrade_source_lock.yaml` 和 `inventory/s100_d4_frontier_lock.yaml` 获取精确版本；在独立目录核对 commit、子模块、许可证和工作树洁净度。

`patches/` 中的补丁应用到锁定的上游提交。应用后必须保留：

- 上游 URL 与基线 commit；
- 补丁文件 SHA-256；
- `git diff --check` 与构建结果；
- 未修改的上游参考副本。

## 板端约束

脚本假设项目工作区位于：

```text
/home/sunrise/workspaces/new_project
```

执行任何板端脚本前：

```bash
cd /home/sunrise/workspaces/new_project
source env.sh
test "${ROS_DOMAIN_ID:-}" = "42"
```

并确认：

- 目标板身份与预期一致；
- `9100–9199` 中所需端口未被占用；
- 冻结服务保持 inactive；
- CAN 保持 down；
- 无相机、雷达、IMU、执行器或机器人访问；
- 任务目录、缓存、依赖、日志和证据都留在新项目目录内。

X5 与 S100 使用独立环境入口和构建/证据目录，不交叉复用板卡二进制。

## 证据等级

| 等级 | 说明 | 能否宣称板端 PASS |
| --- | --- | --- |
| SOURCE_LOCKED | 上游、commit、许可证与哈希已锁定 | 否 |
| HOST_GOLDEN | 主机数值黄金和契约已通过 | 否 |
| BOARD_CPU_PASS | 目标板 CPU 同 fixture 通过 | 仅 CPU |
| BOARD_BPU_PASS | 目标板 BPU 数值、负输入和运行门通过 | 仅该模型/板卡/BPU |
| STRESS_PASS | 冻结持续时间与资源门通过 | 仅当前无外设配置 |
| CLEAN_REBUILD_PASS | 新目录重建与重放通过 | 仅冻结版本 |
| ROBOT_GATE_PASS | 真实硬件分级门有现场证据 | 仅对应 HW 门 |

## 公开证据

公开摘要只保留复算所需的指标、哈希和声明边界。原始日志可能包含网络端点、设备身份、个人绝对路径、第三方二进制或受限模型，因此不进入公共仓库。新增公开证据前先运行：

```bash
python scripts/ci/public_release_audit.py
```
