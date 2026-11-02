"""Local speaker segmentation and session-scoped voice embedding clustering.

Speaker labels are estimates, never identities or gender predictions.
"""

from collections import defaultdict
import numpy as np
import sherpa_onnx
from engine import ROOT, RATE


class SpeakerTracker:
    def __init__(self, threshold=0.45):
        model_dir = ROOT / "models/speakers"
        embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(model_dir / "embedding.onnx"), num_threads=2, provider="cpu"
        )
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(
                        model_dir
                        / "sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx"
                    )
                ),
                num_threads=2,
                provider="cpu",
            ),
            embedding=embedding,
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.7),
            min_duration_on=0.3,
            min_duration_off=0.35,
        )
        if not config.validate():
            raise RuntimeError("Speaker models are missing. Run setup_speakers.py.")
        self.diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(embedding)
        self.centroids = []
        self.threshold = threshold

    def assign(self, samples):
        if len(samples) < 0.8 * RATE:
            return "unknown", True
        stream = self.extractor.create_stream()
        stream.accept_waveform(RATE, samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            return "unknown", True
        vector = np.asarray(self.extractor.compute(stream), dtype=np.float32)
        vector /= max(float(np.linalg.norm(vector)), 1e-10)
        similarities = [float(vector @ center) for center in self.centroids]
        best = int(np.argmax(similarities)) if similarities else -1
        if best >= 0 and similarities[best] >= self.threshold:
            combined = 0.85 * self.centroids[best] + 0.15 * vector
            self.centroids[best] = combined / max(
                float(np.linalg.norm(combined)), 1e-10
            )
            return f"speaker_{best+1}", similarities[best] < 0.55
        self.centroids.append(vector)
        return f"speaker_{len(self.centroids)}", len(samples) < 1.5 * RATE

    def turns(self, samples):
        if len(samples) < 0.5 * RATE:
            return [
                {
                    "start": 0,
                    "end": len(samples) / RATE,
                    "speaker": "unknown",
                    "uncertain": True,
                    "overlap": False,
                }
            ]
        segments = self.diarizer.process(samples).sort_by_start_time()
        groups = defaultdict(list)
        for part in segments:
            groups[part.speaker].append(
                samples[
                    max(0, int(part.start * RATE)) : min(
                        len(samples), int(part.end * RATE)
                    )
                ]
            )
        labels = {}
        for key, parts in groups.items():
            remaining = 8 * RATE
            bounded = []
            for part in parts:
                bounded.append(part[:remaining])
                remaining -= len(bounded[-1])
                if remaining == 0:
                    break
            labels[key] = self.assign(np.concatenate(bounded))
        result = []
        for part in segments:
            if part.end - part.start < 0.25:
                continue
            label, uncertain = labels[part.speaker]
            overlap = any(
                other.speaker != part.speaker
                and min(part.end, other.end) - max(part.start, other.start) > 0.1
                for other in segments
            )
            result.append(
                {
                    "start": max(0, part.start - 0.1),
                    "end": min(len(samples) / RATE, part.end + 0.1),
                    "speaker": label,
                    "uncertain": uncertain or overlap,
                    "overlap": overlap,
                }
            )
        return result
