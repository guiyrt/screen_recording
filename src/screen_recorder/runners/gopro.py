import logging
import urllib.request
import asyncio
from typing import Callable, Final

from .base import BaseRunner
from ..configs import GoProConfig, AudioConfig
from ..encoders import VideoEncoder
from ..audio import AudioTrack

logger = logging.getLogger(__name__)

FOV_MAP: Final[dict[str, int]] = {
    "Wide": 0,
    "Narrow": 2,
    "Superview": 3,
    "Linear": 4
}

class GoProRunner(BaseRunner):
    """Runner specifically for GoPro HERO 8.

    HERO 8 does not support GoPro API 2.0.
    For reference, check https://github.com/jschmid1/gopro_as_webcam_on_linux/blob/master/gopro.

    There is significant delay using this, it is preferred to use normal recording.
    """
    video_cfg: GoProConfig

    def __init__(
        self,
        video_cfg: GoProConfig,
        audio_cfg: AudioConfig,
        encoder: VideoEncoder,
        on_error: Callable[[], None] | None = None,
        log_level: str = "info",
    ) -> None:
        super().__init__(video_cfg, audio_cfg, encoder, on_error, log_level)
        self._ip = self._calculate_gopro_ip(video_cfg.serial_number)
        self._base_url = f"http://{self._ip}:8080/gp/gpWebcam"
    
    @property
    def name(self) -> str:
        return "GOPRO"

    @property
    def requested_audio_tracks(self) -> list[AudioTrack]:
        return [AudioTrack.NATIVE]
    
    @property
    def has_internal_audio(self) -> bool:
        return True

    def _calculate_gopro_ip(self, serial: str) -> str:
        """
        Calculates GoPro USB IP: 172.2X.1YZ.51
        where XYZ are the last three digits of the serial.
        """
        if len(serial) < 3:
            raise ValueError(f"Invalid GoPro Serial Number: {serial}")
            
        # Extract X, Y, Z from the last 3 characters
        x = int(serial[-3])
        y = int(serial[-2])
        z = int(serial[-1])
        
        octet2 = 20 + x
        octet3 = 100 + (y * 10) + z
        
        return f"172.{octet2}.{octet3}.51"

    async def _send_command(self, action: str, params: str = "") -> bool:
        """Sends an HTTP command to the GoPro API."""
        url = f"{self._base_url}/{action}{params}"
        
        # Run synchronous urlopen in a thread to avoid blocking the event loop
        def _fetch():
            try:
                with urllib.request.urlopen(url, timeout=3.0) as response:
                    return response.getcode() == 200
            except Exception as e:
                logger.warning(f"[{self.name}] HTTP {action} failed: {e}")
                return False

        return await asyncio.get_running_loop().run_in_executor(None, _fetch)

    async def pre_start(self):
        """Wakes up the GoPro and starts the webcam stream."""
        logger.info(f"[{self.name}] Waking up GoPro at {self._ip} (Res: {self.video_cfg.resolution}, FOV: {self.video_cfg.fov})")
        
        for attempt in range(1, 4):
            if await self._send_command("/START", f"?res={self.video_cfg.resolution}&port={self.video_cfg.port}"):
                logger.info(f"GoPro start successful on attempt {attempt}.")
                break

            if attempt < 3:
                logger.warning(f"[{self.name}] Wake-up attempt {attempt} failed. Retrying...")
                await asyncio.sleep(1)
        else:
            raise RuntimeError(
                f"Failed to start GoPro webcam service at {self._ip} after 3 attempts. "
                "Ensure the camera is powered on and connected via USB-C."
            )        

        if await self._send_command("/SETTINGS", f"?fov={FOV_MAP[self.video_cfg.fov]}"):
            logger.info(f"[{self.name}] Successfully applied FOV: {self.video_cfg.fov}")
        else:
            logger.warning(f"[{self.name}] Failed to apply FOV: {self.video_cfg.fov}")

    async def post_stop(self):
        """Puts the GoPro sensor to sleep to prevent overheating."""
        logger.info(f"[{self.name}] Putting GoPro sensor to sleep...")
        await self._send_command("/STOP")

    def get_video_input_args(self) -> list[str]:
        return [
            "-thread_queue_size", "4096",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-timeout", "10000000",
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
            "-i", f"udp://0.0.0.0:{self.video_cfg.port}?overrun_nonfatal=1&fifo_size=50000000"
        ]