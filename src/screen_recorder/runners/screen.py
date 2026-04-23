from typing import Callable

from .base import BaseRunner
from ..configs import ScreenConfig, AudioConfig
from ..encoders import VideoEncoder
from ..audio import AudioTrack

class ScreenRunner(BaseRunner):
    video_cfg: ScreenConfig

    def __init__(
        self,
        video_cfg: ScreenConfig,
        audio_cfg: AudioConfig,
        encoder: VideoEncoder,
        on_error: Callable[[], None] | None = None,
        log_level: str = "info",
    ) -> None:
        super().__init__(video_cfg, audio_cfg, encoder, on_error, log_level)

    @property
    def name(self) -> str:
        return "SCREEN"
    
    @property
    def requested_audio_tracks(self) -> list[AudioTrack]:
        return [AudioTrack.MIX, AudioTrack.MIC, AudioTrack.SYS]

    def get_video_input_args(self) -> list[str]:
        return [
            "-f", "x11grab",
            "-thread_queue_size", "4048",
            "-framerate", str(self.video_cfg.fps),
            "-i", self.video_cfg.display
        ]