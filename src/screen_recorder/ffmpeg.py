import asyncio
import logging
from pathlib import Path

from .configs import AppSettings
from .encoders import VideoEncoder, NvencHEVCEncoder, X264Encoder

logger = logging.getLogger(__name__)

def build_command(settings: AppSettings, encoder: VideoEncoder, output_file: Path) -> list[str]:
    """Constructs FFmpeg command with an optimized Master Mix track + Isolated tracks."""
    cmd = [
        "ffmpeg", "-y",
        "-loglevel", settings.logging.level.lower(),
    ]

    # --- 1. INPUTS ---
    input_idx = 0
    audio_inputs = []

    # Video Input
    cmd.extend([
        "-f", "x11grab",
        "-thread_queue_size", "2048",
        "-framerate", str(settings.recording.fps),
        "-i", settings.display
    ])
    input_idx += 1

    # Audio Inputs
    if settings.audio.microphone.enabled:
        cmd.extend([
            "-f", "pulse",
            "-thread_queue_size", "1024",
            "-i", settings.audio.microphone.device
        ]
        )
        audio_inputs.append({"name": "mic", "idx": input_idx})
        input_idx += 1

    if settings.audio.system.enabled:
        cmd.extend([
            "-f", "pulse",
            "-thread_queue_size", "1024",
            "-i", settings.audio.system.device
        ])
        audio_inputs.append({"name": "sys", "idx": input_idx})
        input_idx += 1

    # --- 2. FILTER GRAPH ---
    filters = []
    
    # Video Scaling Logic
    if settings.streaming.enabled:
        filters.append(f"[0:v]split=2[v_file_raw][v_stream_raw]")
        filters.append(f"[v_stream_raw]scale={settings.streaming.output_resolution}:flags=fast_bilinear[v_stream]")
        map_v_file, map_v_stream = "[v_file_raw]", "[v_stream]"
    else:
        map_v_file, map_v_stream = "0:v", None

    # Audio Logic
    file_audio_maps = []
    stream_audio_map = None

    if audio_inputs:
        mix_inputs = []
        
        for src in audio_inputs:
            # Step A: Resample ONCE per track (Computationally expensive)
            resample_tag = f"[a_{src['name']}_res]"
            filters.append(f"[{src['idx']}:a]aresample=48000:async=1{resample_tag}")
            
            # Step B: If we have multiple inputs, split them for the solo tracks
            if len(audio_inputs) > 1:
                file_solo = f"[a_{src['name']}_solo]"
                for_mix = f"[a_{src['name']}_for_mix]"
                filters.append(f"{resample_tag}asplit=2{file_solo}{for_mix}")
                
                mix_inputs.append(for_mix)
                file_audio_maps.append(file_solo) # Adds Track 2 and Track 3
            else:
                # If there's only 1 input, the solo track IS the mix track
                mix_inputs.append(resample_tag)

        # Step C: Create the Master Mix
        if len(mix_inputs) > 1:
            mix_in = "".join(mix_inputs)
            raw_mix = "[a_master_mix_raw]"
            filters.append(f"{mix_in}amix=inputs={len(mix_inputs)}:duration=longest{raw_mix}")
        else:
            raw_mix = mix_inputs[0]

        # Step D: Route the Mix to File and/or Stream
        if settings.streaming.enabled:
            mix_file = "[a_mix_file]"
            mix_stream = "[a_mix_stream]"
            # Split the mix so it can be consumed by both outputs!
            filters.append(f"{raw_mix}asplit=2{mix_file}{mix_stream}")
            
            file_audio_maps.insert(0, mix_file) # Track 1
            stream_audio_map = mix_stream
        else:
            # No stream? Just send the mix to the file
            file_audio_maps.insert(0, raw_mix) # Track 1

    if filters:
        cmd.extend(["-filter_complex", "; ".join(filters)])

    # --- 3. OUTPUT: LOCAL FILE ---
    cmd.extend(["-map", map_v_file])
    for a_map in file_audio_maps:
        cmd.extend(["-map", a_map])
    
    cmd.extend(encoder.file_flags)
    if file_audio_maps:
        cmd.extend(["-c:a", settings.audio.codec, "-b:a", settings.audio.bitrate])

    cmd.extend([
        "-pix_fmt", "yuv420p",
        "-b:v", settings.recording.video_bitrate,
        "-maxrate", settings.recording.max_bitrate,
        "-bufsize", settings.recording.max_bitrate,
        str(output_file)
    ])

    # --- 4. OUTPUT: STREAM ---
    if settings.streaming.enabled:
        cmd.extend(["-map", map_v_stream])
        if stream_audio_map:
            cmd.extend(["-map", stream_audio_map])
        
        cmd.extend(encoder.stream_flags)
        if stream_audio_map:
            cmd.extend(["-c:a", settings.audio.codec, "-b:a", settings.audio.bitrate])
            
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