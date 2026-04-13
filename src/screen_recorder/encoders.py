from abc import ABC, abstractmethod

class VideoEncoder(ABC):
    """Abstract base class for FFmpeg command generation."""

    @property
    @abstractmethod
    def file_flags(self) -> list[str]:
        """Returns encoder-specific flags for local file archival."""
        pass

    @property
    @abstractmethod
    def stream_flags(self) -> list[str]:
        """Returns encoder-specific flags for low-latency network streaming."""
        pass

class NvencHEVCEncoder(VideoEncoder):
    """NVIDIA Hardware Encoder Strategy (Optimized for ATC vectors)."""
    
    @property
    def file_flags(self) -> list[str]:
        return [
            "-c:v", "hevc_nvenc",
            "-preset", "p4",    # Balanced Quality
            "-rc", "vbr",       # Variable Bitrate
            "-cq", "24",        # Quality Target
        ]

    @property
    def stream_flags(self) -> list[str]:
        return [
            "-c:v", "hevc_nvenc",
            "-preset", "p1",    # Ultra-fast
            "-tune", "ull",     # Ultra Low Latency
            "-rc", "cbr",       # Constant Bitrate for network stability
        ]

class X264Encoder(VideoEncoder):
    """CPU Fallback Strategy (Safe defaults for non-GPU hardware)."""
    
    @property
    def file_flags(self) -> list[str]:
        return [
            "-c:v", "libx264",
            "-preset", "ultrafast", # Mandatory to prevent 4K CPU frame drops
            "-crf", "23",
        ]

    @property
    def stream_flags(self) -> list[str]:
        return [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency", # CPU equivalent of ULL
        ]