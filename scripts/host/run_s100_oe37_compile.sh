#!/usr/bin/env bash

set -uo pipefail

WORKSPACE=/workspace
cd "$WORKSPACE"
mkdir -p logs compiled

if ! command -v hb_compile >/dev/null 2>&1; then
  printf 'ERROR: hb_compile is not available in the OE 3.7.0 container\n' >&2
  exit 2
fi

{
  printf 'hb_compile=%s\n' "$(command -v hb_compile)"
  hb_compile --version 2>&1 || true
  python3 - <<'PY'
import importlib
for name in ("horizon_tc_ui", "hmct", "hbdk4"):
    try:
        module = importlib.import_module(name)
        print(f"{name}={getattr(module, '__version__', 'unknown')}")
    except Exception as exc:
        print(f"{name}=IMPORT_ERROR:{exc!r}")
PY
} | tee logs/toolchain_versions.txt

: > logs/compile_status.tsv
if [ -n "${S100_OE_ONLY:-}" ]; then
  configs=("configs/${S100_OE_ONLY}_nashe.yaml")
else
  configs=(configs/*_nashe.yaml)
fi
for config in "${configs[@]}"; do
  model="$(basename "$config" _nashe.yaml)"
  log="logs/compile_${model}.log"
  printf 'COMPILING model=%s config=%s\n' "$model" "$config" | tee "$log"
  hb_compile --config "$config" >>"$log" 2>&1
  rc=$?
  hbm_count="$(find "compiled/$model" -type f -name '*.hbm' 2>/dev/null | wc -l)"
  printf '%s\t%s\t%s\n' "$model" "$rc" "$hbm_count" >> logs/compile_status.tsv
  printf 'COMPILE_DONE model=%s rc=%s hbm_count=%s\n' "$model" "$rc" "$hbm_count" | tee -a "$log"
done

python3 - <<'PY'
import hashlib
import json
from datetime import datetime
from pathlib import Path

root = Path("/workspace")
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
statuses = {}
for line in (root / "logs" / "compile_status.tsv").read_text(encoding="utf-8").splitlines():
    name, rc, count = line.split("\t")
    statuses[name] = (int(rc), int(count))

models = []
for spec in manifest["models"]:
    name = spec["name"]
    hbm_files = sorted((root / "compiled" / name).glob("*.hbm"))
    current_invocation = name in statuses
    rc, count = statuses.get(name, (None, len(hbm_files)))
    artifacts = []
    for path in hbm_files:
        artifacts.append({
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    status = "PASS" if rc in (None, 0) and count == 1 and len(artifacts) == 1 else "CPU_FALLBACK"
    models.append({
        "name": name,
        "compile_exit_code": rc,
        "compile_invocation": "CURRENT" if current_invocation else "REUSED_SAME_WORKSPACE",
        "reported_hbm_count": count,
        "artifacts": artifacts,
        "log": f"logs/compile_{name}.log",
        "status": status,
    })

summary = {
    "schema_version": 1,
    "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "scope": "OpenExplorer 3.7.0 offline ONNX-to-HBM compile; network disabled",
    "target": {"soc": "S100", "march": "nash-e", "runtime": "UCP 3.13.6"},
    "source_manifest": "manifest.json",
    "models": models,
    "counts": {
        "total": len(models),
        "pass": sum(item["status"] == "PASS" for item in models),
        "cpu_fallback": sum(item["status"] == "CPU_FALLBACK" for item in models),
    },
    "external_network": False,
    "external_device_access": False,
    "control_output": False,
}
summary["result"] = "PASS" if summary["counts"]["pass"] > 0 else "FAIL"
(root / "logs" / "conversion_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(summary["counts"], ensure_ascii=False))
if summary["result"] != "PASS":
    raise SystemExit(1)
PY
