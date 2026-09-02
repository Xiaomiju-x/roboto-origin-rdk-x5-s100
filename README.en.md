# Roboto Origin on RDK X5 / S100 / S600

An evidence-driven engineering repository for reproducing the public Roboto Origin stack on RDK X5, migrating its offline baseline to RDK S100, and extending selected algorithms to RDK S600.

> **Project context: this is a formal D-Robotics company project led by company leadership. The repository maintainer contributes as an intern responsible for the relevant algorithm development, offline deployment, and validation work.**

> Exact status: **the X5 offline baseline and U1–U3 upgrades pass; the S100 D0–D6 gate passes; a file-only YOLO shadow pipeline passes in order on real X5, S100, and S600 BPUs. The physical robot, sensors, CAN, motors, navigation, and locomotion remain unvalidated.**

[中文](README.md) · [Results](docs/RESULTS.md) · [Reproduction](docs/REPRODUCIBILITY.md) · [Safety](docs/SAFETY.md) · [Roadmap](docs/ROADMAP.md)

![System overview](docs/assets/system-overview.svg)

## What is included

- Deterministic host and board probes for model, perception, localization, and planning checks.
- Source, dependency, model, and deployment locks with explicit upstream provenance.
- X5 CPU and S100 CPU/BPU numerical A/B runners.
- Negative-input, deadline, process-residue, stress, and clean-rebuild gates.
- A staged HW0–HW5 safety roadmap for later physical integration.
- Redacted machine-readable result summaries.

## Verified scope

| Target | Status | Evidence summary |
| --- | --- | --- |
| X5 official offline baseline O0–O6 | PASS | 10/10 ONNX models, synthetic depth, Nav2 planning, FAST-LIO2 replay, and 8/8 bounded Isaac tasks |
| X5 U1 temporal occupancy/flow | PASS | 32,286 parameters; future occupancy IoU 0.8774; X5 CPU median 15.52 ms |
| X5 U2 trust/action guard | PASS | 5/5 injected fault classes fail closed |
| X5 U3 KISS-ICP | PASS | zero static drift; approximately 0.08 µm endpoint error on a 1.36 m synthetic trajectory |
| S100 D0–D6 offline deployment | PASS | 9 BPU PASS, 1 explicit CPU fallback, 0 FAIL; 4/4 official vision smoke; 30-minute stress; clean rebuild |
| S100 U1 Nash-e BPU A/B | PASS | 128 fixtures; mean cosine 0.999807; BPU p50 0.807 ms |
| YOLO shadow migration: X5→S100→S600 | FUNCTIONAL PASS | X5 native baseline 3/3; S100/S600 YOLO11n 10/10 each; five detections and the same file-only event; strict score parity fails |
| Physical robot HW0–HW5 | NOT STARTED | No camera, lidar, IMU, CAN, serial, motor, or actuator was connected |

See [docs/RESULTS.md](docs/RESULTS.md) for the complete claim boundaries and [docs/S600_YOLO11_MIGRATION.md](docs/S600_YOLO11_MIGRATION.md) for the migration record.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pytest
python scripts/ci/public_release_audit.py
```

Board scripts are stage-specific engineering tools, not a one-command robot installer. Read [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) and [docs/SAFETY.md](docs/SAFETY.md) before running them.

## License and status

Project-owned code is released under the [Apache License 2.0](LICENSE). Third-party repositories, models, datasets, binaries, and generated artifacts retain their own licenses; see [NOTICE](NOTICE) and `inventory/`.

This repository publishes the relevant algorithm implementations and reproducible validation materials maintained within that formal company project. It does not present unvalidated hardware capabilities or production readiness as company commitments.
