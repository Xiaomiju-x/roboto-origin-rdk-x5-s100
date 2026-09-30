"""Loopback-only, bounded developer demo for the S600 coprocessor. No X5/control API."""

import argparse
import json
import os
import secrets
import signal
import subprocess
import threading
import time
import uuid
import wave
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
from vlm_once import infer
from whisper_once import transcribe


def tail_events(path):
    if not path.is_file():
        return []
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 131072))
        lines = f.read().split(b"\n")
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except (ValueError, UnicodeError):
            pass
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--port", type=int, default=9186)
    ap.add_argument("--seconds", type=int, default=900)
    ap.add_argument("--capture-seconds", type=int, default=300)
    args = ap.parse_args()
    root = args.root.resolve()
    out = args.output.resolve()
    if (
        not 9100 <= args.port <= 9199
        or not 60 <= args.seconds <= 1800
        or not 12 <= args.capture_seconds <= 300
    ):
        raise ValueError("bounded settings")
    out.mkdir(parents=True, exist_ok=False)
    token = secrets.token_urlsafe(24)
    jobs = {}
    busy = threading.Lock()
    stopped = threading.Event()
    env = os.environ.copy()
    env.update(
        ROS_DOMAIN_ID="42",
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    commands = {
        "vision": [
            "/usr/bin/python3",
            str(root / "vision.py"),
            "--upstream",
            str(root / "upstream"),
            "--task",
            "seg",
            "--model",
            str(root / "yolo11x_seg_nashp_640x640_nv12.hbm"),
            "--rgbd",
            "--cores",
            "0",
            "--seconds",
            str(args.capture_seconds),
            "--output",
            str(out / "vision"),
        ],
        "sensors": [
            str(root / ".venv/bin/python"),
            str(root / "sensor.py"),
            "--serial",
            "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0",
            "--model",
            str(root / "models/vosk-model-small-cn-0.22"),
            "--seconds",
            str(args.capture_seconds),
            "--output",
            str(out / "sensors"),
            "--audio-tap",
            str(root / "models_whisper_ram/live_ring.pcm"),
        ],
        "planner": [
            "/usr/bin/python3",
            str(root / "scene.py"),
            "--events",
            str(out / "sensors/events.jsonl"),
            "--vision-events",
            str(out / "vision/events.jsonl"),
            "--seconds",
            str(args.capture_seconds + 3),
            "--output",
            str(out / "planner"),
        ],
    }
    children = {}
    logs = {}
    capture_dir = out
    capture_lock = threading.Lock()

    def start_capture(target):
        with capture_lock:
            if not busy.acquire(blocking=False):
                raise RuntimeError("model request is running")
            try:
                launch_capture(target)
            finally:
                busy.release()

    def launch_capture(target):
        nonlocal capture_dir, commands
        if any(p.poll() is None for p in children.values()):
            raise RuntimeError("capture is still running")
        for log in logs.values():
            log.close()
        old_prefix = str(capture_dir) + os.sep
        new_prefix = str(target) + os.sep
        commands = {
            name: [
                new_prefix + v[len(old_prefix) :] if v.startswith(old_prefix) else v
                for v in cmd
            ]
            for name, cmd in commands.items()
        }
        capture_dir = target
        for name, cmd in commands.items():
            logs[name] = (capture_dir / f"{name}.log").open("x")
            children[name] = subprocess.Popen(
                cmd,
                cwd=root,
                stdout=logs[name],
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )

    def read_state():
        frame = {}
        p = capture_dir / "vision/latest_event.json"
        if p.exists():
            try:
                frame = json.loads(p.read_text())
            except ValueError:
                pass
        events = tail_events(capture_dir / "sensors/events.jsonl")
        scan = next((e for e in reversed(events) if e.get("kind") == "scan"), None)
        intents = [e for e in events if e.get("kind") == "intent"][-5:]
        nearest = None
        if scan:
            values = [d for a, d, q in scan["points"] if 0.03 <= d <= 12 and q > 0]
            if values:
                nearest = min(values)
        age = (time.monotonic_ns() - frame.get("host_monotonic_ns", 0)) / 1e9
        return {
            "live": bool(frame) and age < 3,
            "frame_age_seconds": age if frame else None,
            "frame": frame,
            "counts": dict(Counter(o["label"] for o in frame.get("objects", []))),
            "lidar_nearest_native_m": nearest,
            "lidar_frame": "native_uncalibrated",
            "intents": intents,
            "jobs": list(jobs.values())[-8:],
            "processes": {
                name: ("running" if p.poll() is None else p.returncode)
                for name, p in children.items()
            },
            "motion_output": False,
            "x5_baseline": "team_verified_not_retested",
            "geometry_status": "RGB_DEPTH_AND_LIDAR_EXTRINSICS_PENDING",
            "model": "Qwen3-VL-2B-Instruct / S600 BPU",
        }

    def ask(prompt, source="web"):
        if not busy.acquire(blocking=False):
            raise RuntimeError("model busy")
        image = capture_dir / "vision/latest_input.jpg"
        state = read_state()
        if not state["live"] or not image.is_file():
            busy.release()
            raise ValueError("fresh camera frame unavailable")
        job_id = uuid.uuid4().hex
        jobs[job_id] = {
            "id": job_id,
            "state": "running",
            "prompt": prompt,
            "source": source,
            "created": time.time(),
        }

        def worker():
            try:
                # Keep the prompt a text request, never a native CLI command.
                full = (
                    "请根据图片回答以下问题，用简短中文回答，不推测实际距离、不下发运动指令："
                    + prompt
                )
                result = infer(root, image, full, out / "answers" / job_id)
                jobs[job_id].update(state="done", **result)
            except Exception as exc:
                jobs[job_id].update(state="failed", error=str(exc))
            finally:
                busy.release()

        threading.Thread(target=worker, daemon=True).start()
        return job_id

    def listen():
        if not busy.acquire(blocking=False):
            raise RuntimeError("model busy")
        if not read_state()["live"]:
            busy.release()
            raise ValueError("fresh capture unavailable")
        job_id = uuid.uuid4().hex
        jobs[job_id] = {
            "id": job_id,
            "state": "running",
            "prompt": "听5秒并回答",
            "source": "M260C/Whisper-BPU",
            "created": time.time(),
        }

        def worker():
            wav_path = root / "models_whisper_ram" / f"request_{job_id}.wav"
            try:
                time.sleep(5)
                tap = root / "models_whisper_ram/live_ring.pcm"
                metadata = json.loads(tap.with_suffix(".json").read_text())
                if time.monotonic() - metadata["monotonic"] > 1:
                    raise ValueError("audio stale")
                pcm = tap.read_bytes()
                data = np.frombuffer(pcm, "<i2").astype(np.float64)
                rms = float(np.sqrt(np.mean(data * data))) if len(data) else 0
                jobs[job_id]["audio_rms"] = rms
                if rms < 300:
                    jobs[job_id].update(
                        state="done",
                        answer="这5秒没有足够清晰的语音，请在麦克风前说出问题。",
                        speech_status="LOW_ENERGY",
                    )
                    return
                with wave.open(str(wav_path), "wb") as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(pcm)
                result = transcribe(root, wav_path, out / "answers" / job_id / "asr")
                text = result["transcript"]
                jobs[job_id]["asr"] = result
                if not text.strip():
                    raise ValueError("empty transcript")
                image = capture_dir / "vision/latest_input.jpg"
                answer = infer(
                    root,
                    image,
                    "请根据图片用简短中文回答这个语音问题，不推测距离、不执行动作："
                    + text[:200],
                    out / "answers" / job_id / "vlm",
                )
                jobs[job_id].update(
                    state="done", answer=answer["answer"], transcript=text, vlm=answer
                )
            except Exception as exc:
                jobs[job_id].update(state="failed", error=str(exc))
            finally:
                wav_path.unlink(missing_ok=True)
                busy.release()

        threading.Thread(target=worker, daemon=True).start()
        return job_id

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *values):
            if values and str(values[0]).startswith("POST"):
                print("DEMO_REQUEST", values[0], flush=True)

        def reply(self, code, data, kind="application/json"):
            content = (
                data
                if isinstance(data, bytes)
                else json.dumps(data, ensure_ascii=False).encode()
            )
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def local_host(self):
            try:
                host = urlsplit("http://" + self.headers.get("Host", "")).hostname
            except ValueError:
                return False
            return host in ["127.0.0.1", "localhost"]

        def do_GET(self):
            if not self.local_host():
                return self.reply(403, {"error": "host"})
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self.reply(
                    200,
                    (root / "dashboard.html")
                    .read_text()
                    .replace("__TOKEN__", token)
                    .encode(),
                    "text/html; charset=utf-8",
                )
            if path == "/api/state":
                return self.reply(200, read_state())
            if path == "/frame.jpg":
                image = capture_dir / "vision/latest.jpg"
                if image.exists():
                    return self.reply(200, image.read_bytes(), "image/jpeg")
            return self.reply(404, {"error": "not found"})

        def do_POST(self):
            if (
                self.path not in ["/api/ask", "/api/listen", "/api/restart"]
                or not self.local_host()
            ):
                return self.reply(403, {"error": "route/host"})
            if self.headers.get("X-Demo-Token") != token or self.headers.get(
                "Origin"
            ) != "http://" + self.headers.get("Host", ""):
                return self.reply(403, {"error": "origin/token"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2048:
                    raise ValueError("body length")
                body = json.loads(self.rfile.read(size))
                prompt = body.get("prompt", "")
                if self.path == "/api/restart":
                    target = out / ("capture_" + uuid.uuid4().hex[:10])
                    target.mkdir()
                    start_capture(target)
                    return self.reply(200, {"capture": "started"})
                if self.path == "/api/listen":
                    return self.reply(202, {"id": listen()})
                if (
                    not isinstance(prompt, str)
                    or not 1 <= len(prompt) <= 200
                    or any(ord(c) < 32 for c in prompt)
                ):
                    raise ValueError("prompt")
                return self.reply(202, {"id": ask(prompt)})
            except (ValueError, RuntimeError) as exc:
                return self.reply(409, {"error": str(exc)})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.timeout = 0.25
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    seen_intents = set()
    deadline = time.monotonic() + args.seconds
    try:
        start_capture(out)
        print(f"DEMO_READY loopback_port={args.port}", flush=True)
        while not stopped.is_set() and time.monotonic() < deadline:
            server.handle_request()
            for event in tail_events(capture_dir / "sensors/events.jsonl"):
                if (
                    event.get("kind") == "intent"
                    and event.get("intent") == "FIND_TARGET"
                ):
                    key = event.get("monotonic")
                    if key in seen_intents:
                        continue
                    seen_intents.add(key)
                    try:
                        ask(event.get("transcript", "找到目标"), "M260C")
                    except (RuntimeError, ValueError):
                        pass
    finally:
        server.server_close()
        # Let an in-flight bounded model request finish before releasing its parent.
        with busy:
            pass
        for child in children.values():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        for log in logs.values():
            log.close()
        (out / "demo_summary.json").write_text(
            json.dumps(
                {
                    "exits": {n: p.returncode for n, p in children.items()},
                    "jobs": list(jobs.values()),
                    "motion_output": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
