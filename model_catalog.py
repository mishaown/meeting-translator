"""Named, local model configurations shared by the GUI, engines and setup."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRANSCRIPTION_MODELS = {
    "tiny": {
        "name": "Whisper tiny · fastest",
        "file": "ggml-tiny-q5_1.bin",
        "bytes": 32152673,
        "sha256": "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7",
    },
    "base": {
        "name": "Whisper base · fast",
        "file": "ggml-base-q5_1.bin",
        "bytes": 59707625,
        "sha256": "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
    },
    "base-f16": {
        "name": "Whisper base · full precision",
        "file": "ggml-base.bin",
        "bytes": 147951465,
        "sha256": "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe",
    },
    "small": {
        "name": "Whisper small · balanced",
        "file": "ggml-small-q5_1.bin",
        "bytes": 190085487,
        "sha256": "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
    },
    "medium": {
        "name": "Whisper medium · quality",
        "file": "ggml-medium-q5_0.bin",
        "bytes": 539212467,
        "sha256": "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    },
    "turbo": {
        "name": "Whisper large-v3 turbo",
        "file": "ggml-large-v3-turbo-q5_0.bin",
        "bytes": 574041195,
        "sha256": "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    },
}
TRANSLATION_MODELS = {
    "qwen25-1.5b": {
        "name": "Qwen2.5 1.5B · balanced",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "bytes": 1117320736,
        "sha256": "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
    },
    "qwen3-0.6b": {
        "name": "Qwen3 0.6B · fastest, experimental",
        "file": "Qwen3-0.6B-Q8_0.gguf",
        "bytes": 639446688,
        "sha256": "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031",
        "repo": "Qwen/Qwen3-0.6B-GGUF",
    },
    "qwen3-1.7b": {
        "name": "Qwen3 1.7B · quality comparison",
        "file": "Qwen3-1.7B-Q8_0.gguf",
        "bytes": 1834426016,
        "sha256": "061b54daade076b5d3362dac252678d17da8c68f07560be70818cace6590cb1a",
        "repo": "Qwen/Qwen3-1.7B-GGUF",
    },
    "whisper": {"name": "Whisper direct · compare audio translation", "file": None},
}


def model_path(identity, task="transcription", *, require_installed=True):
    catalog = TRANSCRIPTION_MODELS if task == "transcription" else TRANSLATION_MODELS
    if identity not in catalog:
        raise ValueError(f"Unknown {task} model")
    filename = catalog[identity]["file"]
    if filename is None:
        return None
    path = ROOT / "models" / filename
    if require_installed and not path.is_file():
        raise FileNotFoundError(
            f"{catalog[identity]['name']} is not downloaded. Run setup_models.py --models {identity}."
        )
    return path


def catalog():
    def entries(models):
        return [
            dict(
                id=key,
                name=item["name"],
                installed=item["file"] is None
                or (ROOT / "models" / item["file"]).is_file(),
                size_mib=round(item.get("bytes", 0) / 1024**2),
            )
            for key, item in models.items()
        ]

    return {
        "transcription": entries(TRANSCRIPTION_MODELS),
        "translation": entries(TRANSLATION_MODELS),
        "devices": [
            {"id": "auto", "name": "Automatic · GPU when supported"},
            {"id": "cpu", "name": "CPU"},
            {"id": "gpu", "name": "GPU · strict, no CPU fallback"},
        ],
    }
