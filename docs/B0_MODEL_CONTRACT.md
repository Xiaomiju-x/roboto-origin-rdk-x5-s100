# B0 模型与控制契约审计

审计脚本：`scripts/host/audit_model_contract.py`。证据：`evidence/b0/model_contract.json` 与 `model_contract.md`。

该审计只解析锁定源码中的 XML、JSON、YAML 和 Python 配置，不导入 ROS、Isaac Lab、MuJoCo 或驱动，不访问任何板端设备。结果为 `BLOCKED`：21 项通过、4 项警告、10 项失败。

## 已一致

- 规范 URDF 有 23 个唯一非固定关节。
- 规范 URDF、MJCF 和 MJCF actuator 的关节顺序一致。
- 训练仓库的 URDF/MJCF 与规范描述仓库字节一致。
- GMR 的 URDF/MJCF 关节集合和顺序一致，命名 pose 覆盖全部 23 关节。
- 部署 motor ID 为 1-23，四路分组为 `[6, 7, 5, 5]`，控制向量长度均为 23。
- `urdf2motor` 为 0-22 恒等映射；七个策略的 `usd2urdf` 均为同一合法排列。
- 七个策略都配置 `dt=0.004`、`decimation=5`，静态含义为 250 Hz 步长与 50 Hz 策略频率。

## 阻塞差异

1. 规范 URDF 与规范 MJCF 有 15 个关节限位不一致，涉及腿、踝、torso 和手臂。训练副本虽然与两份规范文件分别一致，但两种格式彼此并不一致。
2. GMR 资产的关节顺序一致，但部分 URDF/MJCF 限位与规范 URDF 不一致，不能把动作重定向输出直接视为部署安全范围。
3. 运行时 `robot.yaml` 的 motor index 12（torso）零偏为 `2.093`；`motion_player.yaml` 与 `set_zero.yaml` 对应值为 `0`。
4. 七个策略的 `joint_limits` 都存在超出规范 URDF 的项：`default`、`attn_enc`、`interrupt` 各 6 项；`amp`、`beyondmimic`、`getup`、`parkour` 各 22 项。

以上差异不等价于官方实现必然错误：URDF、MJCF、软件裁剪和电机机械范围可能承担不同角色。但在实机控制前必须明确每一层的权威来源、单位、坐标符号和最终硬限位，不能靠猜测自动合并。

## 解析路线

后续在项目自有目录建立单一候选契约，至少包含：规范关节顺序、motor ID/总线、方向符号、零偏来源、机械限位、训练限位、策略裁剪限位和最终安全夹紧限位。每一项记录来源提交与实物/固件证据。

在实际电机型号、零位、机械限位和固件保护范围确认前，候选契约只用于静态校验，不能部署到驱动。官方 `upstream` 与板端 `src/official` 继续保持只读。
