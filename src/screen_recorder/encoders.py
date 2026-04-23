from abc import ABC, abstractmethod

from .configs import VideoConfig, StreamingConfig

class VideoEncoder(ABC):
    """Abstract base class for FFmpeg command generation."""

    @abstractmethod
    def get_file_flags(self, cfg: VideoConfig) -> list[str]:
        """Returns encoder-specific flags for local file archival."""
        pass

    @abstractmethod
    def get_stream_flags(self, cfg: VideoConfig) -> list[str]:
        """Returns encoder-specific flags for low-latency network streaming."""
        pass

    @abstractmethod
    def get_scaling_filter(self) -> str:
        """Returns the appropriate filter string for resizing video."""
        pass

class NvencHEVCEncoder(VideoEncoder):
    """NVIDIA Hardware Encoder Strategy (Optimized for ATC vectors and fast panning)."""
    
    def get_file_flags(self, cfg: VideoConfig) -> list[str]:
        return [
            "-c:v", "hevc_nvenc",
            "-fps_mode", "cfr", 
            "-pix_fmt", "yuv420p",     # Handle SW conversion for the file output branch
            "-preset", "p6",           # Balanced Quality
            "-rc", "vbr",              # Variable Bitrate allows bursting during panning
            "-cq", str(cfg.cq),        # Quality Target
            "-spatial-aq", "1",        # Enhances sharp edges (crucial for ATC text)
            "-temporal-aq", "1",       # Enhances high-motion scenes (crucial for panning)
            "-b:v", cfg.video_bitrate,
            "-maxrate", cfg.max_bitrate,
            "-bufsize", cfg.max_bitrate, # 1 second buffer for bursting
        ]

    def get_stream_flags(self, cfg: VideoConfig) -> list[str]:
        return [
            "-c:v", "hevc_nvenc",
            # We omit -pix_fmt here so NVENC accepts the CUDA hw surface directly
            "-preset", "p1",           # Ultra-fast
            "-tune", "ull",            # Ultra Low Latency
            "-delay", "0",             # Zero frame delay
            "-zerolatency", "1",
            "-rc", "cbr",              # Constant Bitrate for network stability
            "-b:v", cfg.streaming.bitrate,
            "-maxrate", cfg.streaming.bitrate,
            "-bufsize", cfg.streaming.bitrate,
        ]

    def get_scaling_filter(self, cfg: StreamingConfig):
        w, h = cfg.resolution.split(":")
        
        return (
            "format=yuv420p,"
            f"scale='min({w},iw)':'min({h},ih)':force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            "hwupload_cuda"
        )

class X264Encoder(VideoEncoder):
    """CPU Fallback Strategy (Safe defaults for non-GPU hardware)."""
    
    def get_file_flags(self, cfg: VideoConfig) -> list[str]:
        return [
            "-c:v", "libx264",
            "-fps_mode", "cfr",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast", 
            "-crf", str(cfg.cq),
            "-b:v", cfg.video_bitrate,
            "-maxrate", cfg.max_bitrate,
            "-bufsize", cfg.max_bitrate,
        ]

    def get_stream_flags(self, cfg: VideoConfig) -> list[str]:
        return [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            "-tune", "zerolatency", 
            "-b:v", cfg.streaming.bitrate,
            "-maxrate", cfg.streaming.bitrate,
            "-bufsize", cfg.streaming.bitrate,
        ]
    
    def get_scaling_filter(self, cfg: StreamingConfig):
        w, h = cfg.resolution.split(":")
        
        return (
            "format=yuv420p,"
            f"scale='min({w},iw)':'min({h},ih)':force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        )
