"""Install checksum-pinned local speaker models from the upstream releases."""

import hashlib
import tarfile
import urllib.request
from model_catalog import ROOT

ASSETS = [
    (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        "segmentation.tar.bz2",
        "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
    ),
    (
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_zh_cnceleb_resnet34_LM.onnx",
        "embedding.onnx",
        "87d1d5068397f3792c730570b53d66cd8be1da7ea22dd04f5b6706d96a3cd168",
    ),
]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    directory = ROOT / "models/speakers"
    directory.mkdir(parents=True, exist_ok=True)
    for url, name, checksum in ASSETS:
        target = directory / name
        if not target.exists() or digest(target) != checksum:
            temporary = target.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open(
                "wb"
            ) as file:
                while data := response.read(1024 * 1024):
                    file.write(data)
            if digest(temporary) != checksum:
                raise RuntimeError(f"Incomplete or incorrect speaker model: {name}")
            temporary.replace(target)
        print(f"Verified {name}")
    with tarfile.open(directory / "segmentation.tar.bz2") as archive:
        archive.extractall(directory, filter="data")


if __name__ == "__main__":
    main()
