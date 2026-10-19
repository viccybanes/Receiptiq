"""Evaluate installed local vision on labeled samples; block non-loopback sockets."""

import argparse
import io
import ipaddress
import json
import socket
import time
from pathlib import Path
from contextlib import contextmanager

from app.db import data_directory
from app.ocr import PaddleEngine
from app.system import ensure_runtime, stop_runtime
from app.vision import ALLOWED_MODELS, DEFAULT_MODEL, MAX_RESPONSE_BYTES, VisionEngine, VisionError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=Path("artifacts/samples"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--model", choices=ALLOWED_MODELS, default=DEFAULT_MODEL)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--out", type=Path, default=Path("artifacts/vision-evaluation.json"))
    parser.add_argument("--with-ocr", action="store_true", help="Include real local OCR text as in the receipt workflow")
    parser.add_argument("--manage-runtime", action="store_true", help="Start the installed app runtime and stop it on exit")
    parser.add_argument("--raw-responses", type=Path, help="Developer diagnostic directory for raw local response JSON")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    manifest = json.loads((args.samples / "manifest.json").read_text(encoding="utf-8"))[:args.limit]
    engine = VisionEngine(port=args.port, timeout=args.timeout)
    root = data_directory()
    ocr = PaddleEngine(root) if args.with_ocr else None
    if args.raw_responses:
        args.raw_responses.mkdir(parents=True, exist_ok=True)
        original_open = engine.opener.open
        capture_file = None

        @contextmanager
        def capture(request, *values, **options):
            with original_open(request, *values, **options) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                capture_file.write_bytes(raw)
                with io.BytesIO(raw) as buffered:
                    yield buffered

        engine.opener.open = capture
    original_connect = socket.socket.connect
    records = []
    started = time.monotonic()

    def loopback_only(sock, address):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise OSError("Non-loopback connections disabled during local vision evaluation")
        return original_connect(sock, address)

    try:
        if args.manage_runtime:
            ensure_runtime(root)
        socket.socket.connect = loopback_only
        for index, sample in enumerate(manifest):
            record = {"file": sample["file"], "category": sample["category"]}
            try:
                path = args.samples / sample["file"]
                lines = ocr.recognize(path) if ocr else []
                if args.raw_responses:
                    capture_file = args.raw_responses / (Path(sample["file"]).stem + ".json")
                result = engine.extract([path], lines, model=args.model)
                data = result["draft"]
                record.update(actual=data, seconds=result["seconds"], provenance=result["provenance"],
                              ocr_line_count=len(lines),
                              field_matches={key: data.get(key) == value for key, value in sample["expected"].items()},
                              item_count_matches=len(data["items"]) == sample["expected_item_count"])
                print(f"{index + 1}/{len(manifest)} {sample['category']}: "
                      f"{sum(record['field_matches'].values())}/{len(record['field_matches'])} fields, "
                      f"{result['seconds']}s", flush=True)
            except VisionError as exc:
                record["error"] = str(exc)
                print(f"{index + 1}/{len(manifest)} {sample['category']}: {exc}", flush=True)
            records.append(record)
    finally:
        socket.socket.connect = original_connect
        if args.manage_runtime:
            stop_runtime(root)
    report = {"kind": "Synthetic typography benchmark; does not measure real handwriting accuracy",
              "model": args.model, "outbound_network": "non-loopback sockets blocked during inference",
              "ocr_context": args.with_ocr, "count": len(records),
              "seconds": round(time.monotonic() - started, 3), "records": records}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.out}", flush=True)
    return 1 if any("error" in record for record in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
