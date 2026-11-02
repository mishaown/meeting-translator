"""Compare installed models on the same local audio and Chinese text (no recording)."""

import argparse
import json
import time
from engine import ROOT, RATE, Whisper
from translator import TextTranslator
from meeting import load_audio
from model_catalog import catalog, model_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", default=str(ROOT / "fixtures/chinese-meeting.wav"))
    parser.add_argument("--seconds", type=float, default=4)
    parser.add_argument("--device", choices=["auto", "cpu", "gpu"], default="auto")
    parser.add_argument(
        "--text", default="大家好，今天下午三点开会，请准备好项目进度报告。"
    )
    args = parser.parse_args()
    if not 0 < args.seconds <= 300:
        parser.error("--seconds must be between 0 and 300")
    samples = load_audio(args.audio, 0, int(args.seconds * RATE))
    (ROOT / "reports").mkdir(exist_ok=True)
    report = {
        "audio": str(args.audio),
        "audio_seconds": len(samples) / RATE,
        "translation_input": args.text,
        "requested_device": args.device,
        "results": [],
        "note": "A short functionality and timing check, not an accuracy score. Compare representative meeting audio in the GUI.",
    }
    for task in ("transcription", "translation"):
        for item in catalog()[task]:
            if not item["installed"] or item["id"] == "whisper":
                continue
            row = {"task": task, "model": item["id"]}
            try:
                directory = ROOT / "reports/model-check" / item["id"]
                start = time.perf_counter()
                runtime = (
                    Whisper(item["id"], log_dir=directory, device=args.device)
                    if task == "transcription"
                    else TextTranslator(
                        log_dir=directory,
                        model=model_path(item["id"], task).name,
                        device=args.device,
                    )
                )
                with runtime:
                    row["load_seconds"] = round(time.perf_counter() - start, 3)
                    result = (
                        runtime.infer(samples)
                        if task == "transcription"
                        else runtime.translate(args.text)
                    )
                    row.update({key: result[key] for key in ("text", "seconds")})
                    row["device"] = runtime.device_info()
            except Exception as error:
                row["error"] = str(error)
            report["results"].append(row)
            print(json.dumps(row, ensure_ascii=True), flush=True)
            (ROOT / "reports/model-check.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )


if __name__ == "__main__":
    main()
