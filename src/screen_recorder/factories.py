import asyncio
import logging

from .configs import AppSettings
from .encoders import VideoEncoder, NvencHEVCEncoder, X264Encoder

logger = logging.getLogger(__name__)


async def get_encoder_strategy(settings: AppSettings) -> VideoEncoder:
    """Factory function: Performs a dummy encode to verify hardware, returns cached Strategy."""
    if settings.mode == "gpu":
        logger.info("Verifying GPU hardware availability...")

        # DUMMY ENCODE: Generate 1 frame of black video and try to encode it with NVENC.
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", 
            "-f", "lavfi",
            "-i", "color=size=1920x1080:rate=1", 
            "-c:v", "hevc_nvenc",
            "-frames:v", "1",
            "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

        if proc.returncode == 0:
            logger.info("GPU HEVC Encoder verified successfully.")
            return NvencHEVCEncoder()
            
        logger.warning("GPU hardware check failed, falling back to X264Encoder.")

    return X264Encoder()