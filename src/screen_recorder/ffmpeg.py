import asyncio
import logging
from pathlib import Path

from .configs import AppSettings
from .encoders import VideoEncoder, NvencHEVCEncoder, X264Encoder

logger = logging.getLogger(__name__)

def build_command(settings: AppSettings, encoder: VideoEncoder, output_file: Path) -> list[str]:
    """Constructs the complete FFmpeg command, prioritizing the local file."""
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", settings.logging.level.lower(),
        "-f", "x11grab",
        "-thread_queue_size", "2048",
        "-framerate", str(settings.recording.fps),
        "-i", settings.display,
    ]

    if settings.audio.enabled:
        logger.info(f"Audio recording enabled ({settings.audio.device})")
        cmd.extend([
            "-f", "pulse",
            "-thread_queue_size", "1024",
            "-i", settings.audio.device,
            "-af", "aresample=async=1"
        ])
        # The video is input 0, the audio is input 1.
        audio_map = ["-map", "1:a"]
        audio_encode = ["-c:a", settings.audio.codec, "-b:a", settings.audio.bitrate]
    else:
        audio_map = []
        audio_encode = []

    # --- SIMPLIFIED FILTER GRAPH ---
    if settings.streaming.enabled:
        # Split into two paths, and fast-scale the stream path
        scale_filter = f"scale={settings.streaming.output_resolution}:flags=fast_bilinear"
        filter_complex = f"[0:v]split=2[v_file][v_raw_stream]; [v_raw_stream]{scale_filter}[v_stream]"
        cmd.extend(["-filter_complex", filter_complex])
        map_file, map_stream = "[v_file]", "[v_stream]"
    else:
        map_file, map_stream = "0:v", None

    # --- LOCAL FILE (PRIORITY) ---
    cmd.extend(["-map", map_file])
    cmd.extend(audio_map)
    cmd.extend(encoder.file_flags)
    cmd.extend(audio_encode)
    cmd.extend([
        "-pix_fmt", "yuv420p",
        "-b:v", settings.recording.video_bitrate,
        "-maxrate", settings.recording.max_bitrate,
        "-bufsize", settings.recording.max_bitrate,
        str(output_file)
    ])

    # --- NETWORK STREAM (NEGLECTABLE) ---
    if map_stream:
        cmd.extend(["-map", map_stream])
        cmd.extend(audio_map) 
        cmd.extend(encoder.stream_flags)
        cmd.extend(audio_encode)
        cmd.extend([
            "-pix_fmt", "yuv420p",
            "-b:v", settings.streaming.bitrate,
            "-maxrate", settings.streaming.bitrate,
            "-bufsize", settings.streaming.bitrate,
            "-g", str(settings.streaming.fps),
            
            # FIFO Muxer, runs the network stream in a background thread.
            # If network lags, it drops packets instead of slowing down the file recording.
            "-f", "fifo",
            "-fifo_format", "mpegts",
            "-drop_pkts_on_overflow", "1",
            "-attempt_recovery", "1",
            "-recovery_wait_time", "1",
            
            settings.streaming.url
        ])

    return cmd

async def get_encoder_strategy(settings: AppSettings) -> VideoEncoder:
    """Factory function: Performs a dummy encode to verify hardware, returns cached Strategy."""
    if settings.mode == "gpu":
        logger.info("Verifying GPU hardware availability...")
        
        # DUMMY ENCODE: Generate 1 frame of black video and try to encode it with NVENC.
        # If the GPU driver/hardware is missing, this will fail.
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", 
            "-f", "lavfi", "-i", "color=size=1920x1080:rate=1", 
            "-c:v", "hevc_nvenc", 
            "-frames:v", "1", 
            "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()

        if proc.returncode == 0:
            logger.info("GPU HEVC Encoder verified successfully, using NvencHEVCEncoder.")
            return NvencHEVCEncoder()
            
        logger.warning("GPU hardware check failed, falling back to X264Encoder.")

    return X264Encoder()