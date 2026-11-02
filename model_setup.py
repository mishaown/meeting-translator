"""User-initiated, serialized model installation shared by browser and desktop apps."""

import threading

from app_paths import DATA_ROOT
from inference_runtime import candidates
from model_catalog import TRANSCRIPTION_MODELS, TRANSLATION_MODELS


class ModelSetup:
    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.state = {
            "state": "idle",
            "message": "Choose models to download.",
            "error": None,
        }

    def snapshot(self):
        with self.lock:
            return dict(self.state, data_directory=str(DATA_ROOT))

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, options):
        profile = options.get("profile", "default")
        speakers = options.get("speakers", True)
        extra = options.get("models", [])
        allowed = set(TRANSCRIPTION_MODELS) | (set(TRANSLATION_MODELS) - {"whisper"})
        if profile not in ("default", "lightweight") or not isinstance(speakers, bool):
            raise ValueError("Choose a valid model profile and speaker option.")
        if (
            not isinstance(extra, list)
            or len(extra) > len(allowed)
            or any(not isinstance(item, str) or item not in allowed for item in extra)
        ):
            raise ValueError("Choose model IDs from the supported catalog.")
        with self.app.lock, self.lock:
            if self.app.busy() or self.state["state"] == "running":
                raise ValueError(
                    "Finish the current recording, processing or download first."
                )
            self.update(
                state="running", message="Preparing model downloads...", error=None
            )
            self.thread = threading.Thread(
                target=self.install, args=(profile, speakers, extra), daemon=True
            )
            self.thread.start()
        return self.snapshot()

    def install(self, profile, speakers, extra):
        try:
            import setup_models
            from platform_support import target_platform

            # Releases include native runtimes; source users can install them here too.
            try:
                for task in ("transcription", "translation"):
                    candidates(task, "cpu")
                runtimes = []
            except FileNotFoundError:
                runtimes = setup_models.configure_platform(target_platform())
            models = (
                ["tiny"] if profile == "lightweight" else ["base", "qwen25-1.5b"]
            ) + extra
            for artifact in dict.fromkeys([*runtimes, "vad", *models]):
                self.update(message=f"Downloading and verifying {artifact}...")
                setup_models.download(artifact)
            if "whisper-source" in runtimes:
                self.update(message="Building the speech runtime...")
                setup_models.build_mac_whisper()
            if speakers:
                import setup_speakers

                self.update(message="Downloading and verifying speaker models...")
                setup_speakers.main()
            with self.app.lock:
                self.app.defaults.update(
                    model="tiny" if profile == "lightweight" else "base",
                    translation_model=(
                        "whisper" if profile == "lightweight" else "qwen25-1.5b"
                    ),
                    diarization=speakers,
                )
            self.update(
                state="complete", message="Models are ready. You can start recording."
            )
        except Exception as error:
            self.update(
                state="error",
                error=str(error),
                message="Setup stopped. Retry to resume verified downloads.",
            )
