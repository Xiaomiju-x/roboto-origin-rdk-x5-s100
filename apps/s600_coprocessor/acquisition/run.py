"""Bounded STL-19P + M260C/Vosk file-only demo. No sockets or serial writes.
Protocol reference: https://github.com/ldrobotSensorTeam/ldlidar_sdk
"""

import argparse
import copy
import fcntl
import json
import os
import queue
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

from core import crc8, decide, observe


def idle(device):
    p = subprocess.run(
        ["sudo", "-n", "fuser", device], capture_output=True, timeout=5, check=False
    )
    if p.returncode != 1 or p.stdout or p.stderr:
        raise RuntimeError("Device busy or owner check failed: " + device)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--audio-tap", help="Optional transient PCM ring in a caller-provided RAM directory")
    ap.add_argument("--seconds", type=int, default=60, choices=range(5, 301))
    args = ap.parse_args()
    if os.environ.get("ROS_DOMAIN_ID") != "42":
        raise RuntimeError("Expected isolated environment")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    from vosk import KaldiRecognizer, Model

    model = Model(args.model)
    recognizer = KaldiRecognizer(model, 16000)
    recognizer.SetWords(True)
    audio_queue = queue.Queue(maxsize=40)
    results = queue.Queue()

    def decode():
        try:
            while True:
                item = audio_queue.get()
                if item is None:
                    break
                if recognizer.AcceptWaveform(item):
                    results.put(json.loads(recognizer.Result()))
        except Exception as exc:  # noqa: BLE001 -- propagate worker failures to owner for cleanup
            results.put({"error": str(exc)})

    worker = threading.Thread(target=decode, daemon=True)
    worker.start()
    idle(args.serial)
    idle("/dev/snd/pcmC2D0c")
    serial = os.open(args.serial, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    original = termios.tcgetattr(serial)
    audio = None
    statistics = {
        "crc_ok": 0,
        "crc_bad": 0,
        "scans": 0,
        "audio_bytes": 0,
        "accepted_intents": 0,
        "serial_tx_bytes": 0,
        "motion_output": False,
        "raw_audio_saved": bool(args.audio_tap),
        "raw_audio_retention": "RAM_RING_REQUESTED" if args.audio_tap else "NONE",
        "status": "INCOMPLETE",
    }
    stopped = False
    audio_ring = bytearray()
    tap = Path(args.audio_tap) if args.audio_tap else None
    if tap is not None:
        import ctypes
        probe = ctypes.create_string_buffer(256)
        if ctypes.CDLL(None).statfs(str(tap.parent.resolve()).encode(), probe) != 0 or ctypes.c_long.from_buffer(probe).value != 0x01021994:
            raise RuntimeError("audio tap requires an existing tmpfs RAM directory")

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        fcntl.ioctl(serial, termios.TIOCEXCL)
        cfg = copy.deepcopy(original)
        cfg[0] = cfg[1] = cfg[3] = 0
        cfg[2] = (
            termios.CS8 | termios.CREAD | termios.CLOCAL | (original[2] & termios.HUPCL)
        )
        cfg[4] = cfg[5] = termios.B230400
        cfg[6][termios.VMIN] = cfg[6][termios.VTIME] = 0
        termios.tcsetattr(serial, termios.TCSANOW, cfg)
        with (
            (output / "audio.log").open("x") as log,
            (output / "events.jsonl").open("x") as events,
        ):
            audio = subprocess.Popen(
                [
                    "arecord",
                    "-q",
                    "-D",
                    "hw:CARD=XFMDPV0018,DEV=0",
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-r",
                    "16000",
                    "-c",
                    "1",
                ],
                stdout=subprocess.PIPE,
                stderr=log,
            )
            start = time.monotonic()
            print("READY: microphone and lidar active", flush=True)
            buffer = bytearray()
            points, latest = [], []
            stamp, last_angle = float("-inf"), None
            aligned = False
            last_health = 0
            while not stopped and time.monotonic() - start < args.seconds:
                if audio.poll() is not None:
                    raise RuntimeError("Audio process exited")
                ready = select.select([serial, audio.stdout], [], [], 0.1)[0]
                if serial in ready:
                    buffer.extend(os.read(serial, 4096))
                    while len(buffer) >= 47:
                        if buffer[:2] != b"\x54\x2c":
                            del buffer[0]
                            continue
                        frame = bytes(buffer[:47])
                        if crc8(frame[:-1]) != frame[-1]:
                            statistics["crc_bad"] += 1
                            points, aligned = [], False
                            del buffer[0]
                            continue
                        del buffer[:47]
                        statistics["crc_ok"] += 1
                        begin = struct.unpack_from("<H", frame, 4)[0] / 100
                        end = struct.unpack_from("<H", frame, 42)[0] / 100
                        if not 0 <= begin < 360 or not 0 <= end < 360:
                            continue
                        for i in range(12):
                            angle = (begin + ((end - begin) % 360) * i / 11) % 360
                            distance, intensity = struct.unpack_from(
                                "<HB", frame, 6 + i * 3
                            )
                            if (
                                last_angle is not None
                                and last_angle > 300
                                and angle < 60
                            ):
                                if aligned:
                                    latest, stamp = points, time.monotonic()
                                    statistics["scans"] += 1
                                    events.write(
                                        json.dumps(
                                            {
                                                "kind": "scan",
                                                "monotonic": stamp,
                                                "points": latest,
                                            }
                                        )
                                        + "\n"
                                    )
                                aligned, points = True, []
                            points.append((angle, distance / 1000, intensity))
                            if len(points) > 2000:
                                points, aligned = [], False
                            last_angle = angle
                if audio.stdout in ready:
                    data = os.read(audio.stdout.fileno(), 8000)
                    statistics["audio_bytes"] += len(data)
                    if tap is not None:
                        audio_ring.extend(data)
                        if len(audio_ring) > 160000:
                            del audio_ring[:-160000]
                        tmp = tap.with_suffix(".tmp")
                        tmp.write_bytes(audio_ring)
                        os.replace(tmp, tap)
                        meta = tap.with_suffix(".json")
                        meta_tmp = meta.with_suffix(".tmp.json")
                        meta_tmp.write_text(json.dumps({"monotonic": time.monotonic(), "bytes": len(audio_ring), "rate": 16000}))
                        os.replace(meta_tmp, meta)
                    if not data:
                        raise RuntimeError("Audio EOF")
                    audio_queue.put_nowait(data)
                while not results.empty():
                    result = results.get_nowait()
                    if "error" in result:
                        raise RuntimeError(result["error"])
                    words = result.get("result", [])
                    confidence = min((w["conf"] for w in words), default=0)
                    event = decide(
                        result.get("text", ""),
                        confidence,
                        observe(latest, stamp, time.monotonic()),
                    )
                    statistics["accepted_intents"] += event["intent"] != "REJECTED"
                    event.update(
                        kind="intent", monotonic=time.monotonic(), confidence=confidence
                    )
                    events.write(json.dumps(event, ensure_ascii=False) + "\n")
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                now = time.monotonic()
                if now - last_health >= 1:
                    events.write(
                        json.dumps(
                            {
                                "kind": "health",
                                "monotonic": now,
                                "lidar": observe(latest, stamp, now)["status"],
                            }
                        )
                        + "\n"
                    )
                    events.flush()
                    last_health = now
            statistics["duration_s"] = time.monotonic() - start
            statistics["status"] = "INTERRUPTED" if stopped else "COMPLETED"
            (output / "last_scan.json").write_text(json.dumps(latest))
    finally:
        if audio is not None:
            if audio.poll() is None:
                audio.terminate()
            try:
                audio.wait(timeout=3)
            except subprocess.TimeoutExpired:
                audio.kill()
                audio.wait()
            audio.stdout.close()
        try:
            audio_queue.put_nowait(None)
        except queue.Full:
            pass
        worker.join(timeout=3)
        statistics["decoder_finished"] = not worker.is_alive()
        termios.tcsetattr(serial, termios.TCSANOW, original)
        statistics["serial_restored"] = termios.tcgetattr(serial) == original
        fcntl.ioctl(serial, termios.TIOCNXCL)
        os.close(serial)
        (output / "summary.json").write_text(json.dumps(statistics, indent=2))
        if tap is not None:
            tap.unlink(missing_ok=True)
            tap.with_suffix(".json").unlink(missing_ok=True)
    idle(args.serial)
    idle("/dev/snd/pcmC2D0c")
    print(json.dumps(statistics), flush=True)


if __name__ == "__main__":
    main()
