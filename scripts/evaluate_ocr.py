"""Run real OCR against labeled synthetic inputs with outbound sockets disabled."""

import argparse
import json
import socket
import time
from pathlib import Path

import psutil
from PIL import Image

from app.db import data_directory
from app.extraction import parse
from app.imaging import preprocess
from app.ocr import PaddleEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=Path("artifacts/samples"))
    parser.add_argument("--limit", type=int, default=60)
    args = parser.parse_args()
    manifest = json.loads((args.samples / "manifest.json").read_text(encoding="utf-8"))[:args.limit]
    engine = PaddleEngine(data_directory())
    records = []
    process = psutil.Process()
    peak = process.memory_info().rss
    began = time.monotonic()
    original_connect = socket.socket.connect

    def offline(*args, **kwargs):
        raise OSError("Outbound network disabled for OCR acceptance check")

    socket.socket.connect = offline
    try:
        for index, sample in enumerate(manifest):
            started = time.monotonic()
            with Image.open(args.samples / sample["file"]) as source:
                original, enhanced, _ = preprocess(source.convert("RGB"), 0)
            path = args.samples / "_evaluation.png"
            enhanced.save(path)
            original.close()
            enhanced.close()
            lines = [{**line, "page": 0, "id": str(i)} for i, line in enumerate(engine.recognize(path))]
            data, _ = parse(lines)
            record = {**sample, "actual": data, "seconds": round(time.monotonic() - started, 3),
                      "field_matches": {key: data[key] == value for key, value in sample["expected"].items()},
                      "item_count_matches": len(data["items"]) == sample["expected_item_count"]}
            records.append(record)
            peak = max(peak, process.memory_info().rss)
            print(f"{index + 1}/{len(manifest)} {sample['category']}: {sum(record['field_matches'].values())}/5 fields", flush=True)
    finally:
        socket.socket.connect = original_connect
        (args.samples / "_evaluation.png").unlink(missing_ok=True)
    report = {"kind": "Synthetic typography benchmark; does not measure real handwriting accuracy",
              "outbound_network": "disabled during recognition", "count": len(records),
              "seconds": round(time.monotonic() - began, 3), "peak_observed_rss_mb": round(peak / 1024 ** 2, 1),
              "records": records}
    target = args.samples.parent / "ocr-evaluation.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {target}; {report['seconds']}s; observed peak {report['peak_observed_rss_mb']} MB", flush=True)


if __name__ == "__main__":
    main()
