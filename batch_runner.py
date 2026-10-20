from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path

from incubrix.config import Settings
from incubrix.jobs import JobProcessor
from incubrix.storage import JobStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate videos for a batch of UTF-8 script files.")
    parser.add_argument("inputs", nargs="+", type=Path, help="One or more .txt script files")
    parser.add_argument("--style", default="documentary")
    parser.add_argument("--ratio", choices=["16:9", "9:16", "1:1"], default="16:9")
    args = parser.parse_args()
    settings = Settings.from_env()
    store = JobStore(settings.database_path)
    processor = JobProcessor(settings, store)
    failed = 0
    for script_path in args.inputs:
        if script_path.suffix.lower() != ".txt" or not script_path.is_file():
            print(f"SKIP {script_path}: expected an existing .txt file")
            failed += 1
            continue
        job_id = str(uuid.uuid4())
        request = {
            "script": script_path.read_text(encoding="utf-8"),
            "style": args.style,
            "aspect_ratios": [args.ratio],
            "burn_captions": True,
            "include_srt": True,
        }
        payload = {"request": request, "batch_input": str(script_path)}
        store.create_job(job_id, payload, None)
        processor.run(job_id, payload)
        while True:
            record = store.get_job(job_id)
            if record["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.5)
        if record["status"] == "succeeded":
            print(f"OK {script_path} -> {settings.output_dir / job_id}")
        else:
            print(f"FAILED {script_path}: {record['error']['message']}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
