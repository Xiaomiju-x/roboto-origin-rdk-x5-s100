# Roboto Origin S100 offline workspace

This project is the independent RDK S100/Nash-e adaptation of the completed X5
offline baseline. It is deliberately isolated at:

`/home/sunrise/workspaces/new_project/roboto_origin_s100`

Start a project shell with:

```bash
cd /home/sunrise/workspaces/new_project/roboto_origin_s100
source scripts/env_s100.sh
bash scripts/verify_s100_safety.sh
```

Safety boundaries:

- ROS Domain is always 42 and application ports are limited to 9140-9199.
- No robot, CAN, serial, camera, lidar, IMU, motor, or other peripheral is used.
- Algorithm outputs go only to evidence files or explicitly named capture topics.
- No system package, network, SSH/VNC service, boot, udev, kernel, or firmware
  change belongs to this project.
- Stop project processes and stop sourcing this overlay to roll back. The common
  workspace and the X5 project remain untouched.

Evidence status must distinguish CPU, BPU, fallback, blocked, and not-run. A
single-board offline PASS does not claim real-robot feasibility.

Current offline delivery status (2026-08-29):

- D0-D6: PASS.
- Official D3 deployment: 9 Nash-e BPU PASS, 1 explicit CPU fallback, 0 FAIL.
- Project U1 temporal BEV extension: Nash-e BPU PASS on 128 frozen tests.
- D4 official model smoke: 4/4 PASS.
- D5 literal 30-minute stress: PASS with zero inference errors.
- Canonical host summary: `evidence/host/s100_offline_summary_latest.json`.
- Full handoff and future hardware gates: `docs/S100_OFFLINE_DELIVERY.md`.
