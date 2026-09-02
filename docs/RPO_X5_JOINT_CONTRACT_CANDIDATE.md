# RPO X5 joint contract candidate

Status: **NON-DEPLOYABLE / STATIC USE ONLY**

This project-owned artifact does not choose a physical authority. Its source intersection is diagnostic, not a motion limit.

- Joints: 23
- Published-limit conflicts: 15
- Zero-offset conflicts: 1
- Observed bus state: only can0 enumerated and DOWN; official can1-can3 not observed

## Limit conflicts

| Joint | Static source intersection (rad) |
| --- | --- |
| left_thigh_roll_joint | [-0.2, 1] |
| left_thigh_pitch_joint | [-1, 0.7854] |
| left_knee_joint | [0, 2.5] |
| left_ankle_pitch_joint | [-0.6, 0.6] |
| right_thigh_roll_joint | [-1, 0.2] |
| right_thigh_pitch_joint | [-1, 0.7854] |
| right_knee_joint | [0, 2.5] |
| right_ankle_pitch_joint | [-0.6, 0.6] |
| torso_joint | [-2.62, 2.62] |
| left_arm_pitch_joint | [-3.14, 1.57] |
| left_arm_yaw_joint | [-1.57, 1.57] |
| left_elbow_pitch_joint | [-0.6, 1.57] |
| right_arm_pitch_joint | [-3.14, 1.57] |
| right_arm_yaw_joint | [-1.57, 1.57] |
| right_elbow_pitch_joint | [-0.6, 1.57] |

## Zero-offset conflicts

- `torso_joint`: {'runtime_robot_yaml': 2.093, 'motion_player_yaml': 0.0, 'set_zero_template': 0.0}

## Unresolved gates

- mechanical joint ranges and hard stops not physically verified
- motor identity, firmware limits, sign, and zero calibration not verified
- torso runtime zero offset conflicts with motion/set-zero templates
- official four-interface CAN topology is not present
- emergency stop, support fixture, wiring, polarity, termination, IDs, and bitrate not verified
