import re
import json
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Callable
from pathlib import Path
from datetime import datetime, timezone

from ..configs import VideoConfig, AudioConfig
from ..encoders import VideoEncoder
from ..audio import build_audio_args, AudioTrack, AudioResult

logger = logging.getLogger(__name__)

class BaseRunner(ABC):
    def __init__(
        self,
        video_cfg: VideoConfig,
        audio_cfg: AudioConfig,
        encoder: VideoEncoder,
        on_error: Callable[[], None] | None = None,
        log_level: str = "info",
    ) -> None:
        self.video_cfg = video_cfg
        self.audio_cfg = audio_cfg
        self.encoder = encoder
        self.on_error = on_error
        self.log_level = log_level.lower()

        self.process: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self._metadata_written = False
        
        # State populated during start()
        self.session_path: Path | None = None
        self.timestamp: str | None = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Log prefix and metadata identifier (e.g., 'SCREEN' or 'GOPRO')."""
        pass

    @property
    @abstractmethod
    def requested_audio_tracks(self) -> list['AudioTrack']: # from ..audio import AudioTrack
        """Subclass provides: The specific audio tracks to record, in desired order."""
        pass

    @property
    def supports_native_audio(self) -> bool:
        """Override in subclasses that provide their own audio stream (e.g., GoPro)."""
        return False

    @abstractmethod
    def get_video_input_args(self) -> list[str]:
        """Subclass provides: ['-f', 'x11grab', ...]"""
        pass

    async def pre_start(self):
        """Optional hook for hardware initialization."""
        pass

    async def post_stop(self):
        """Optional hook for hardware teardown."""
        pass

    def _build_inputs(self) -> tuple[list[str], 'AudioResult']:
        cmd = self.get_video_input_args()
        
        include_native = getattr(self.video_cfg, "record_native_audio", False) and self.supports_native_audio
        audio = build_audio_args(self.audio_cfg, self.requested_audio_tracks, start_index=1, include_native=include_native)
        cmd.extend(audio.inputs)
        
        return cmd, audio

    def _build_filters(self, audio: 'AudioResult') -> tuple[list[str], str, str | None, str | None]:
        filters = audio.filters.copy()
        
        source_v_map = "0:v:0"
        v_file_map = source_v_map
        v_stream_map = None
        a_stream_map = None

        if self.video_cfg.streaming.enabled:
            v_file_map, v_stream_map = "[v_file]", "[v_stream]"
            v_filter = f"[{source_v_map}]split=2[v_file][v_s_raw]; "
            v_filter += f"[v_s_raw]{self.encoder.get_scaling_filter(self.video_cfg.streaming)}[v_stream]"
            filters.append(v_filter)
            
            # Split the PRIMARY audio track (maps[0]) for the stream
            if audio.maps:
                orig_a_map = audio.maps[0]
                clean_name = orig_a_map.strip("[]")
                a_file_map = f"[{clean_name}_file]"
                a_stream_map = f"[{clean_name}_stream]"
                filters.append(f"{orig_a_map}asplit=2{a_file_map}{a_stream_map}")
                
                # Replace the primary file track map so it uses the split pad
                audio.maps[0] = a_file_map

        cmd_filters = ["-filter_complex", "; ".join(filters)] if filters else []
        return cmd_filters, v_file_map, v_stream_map, a_stream_map

    def _build_file_output(self, output_file: Path, v_file_map: str, audio: 'AudioResult') -> list[str]:
        cmd = ["-map", v_file_map]
        for a_map in audio.maps:
            cmd.extend(["-map", a_map])
            
        cmd.extend(self.encoder.get_file_flags(self.video_cfg))

        if audio.maps:
            cmd.extend(["-c:a", self.audio_cfg.codec, "-b:a", self.audio_cfg.bitrate])
            
        cmd.append(str(output_file))
        return cmd

    def _build_stream_output(self, v_stream_map: str, a_stream_map: str | None) -> list[str]:
        cmd = ["-map", v_stream_map]
        if a_stream_map:
            cmd.extend(["-map", a_stream_map])
            
        cmd.extend(self.encoder.get_stream_flags(self.video_cfg))

        if a_stream_map:
            cmd.extend(["-c:a", self.audio_cfg.codec, "-b:a", self.audio_cfg.bitrate])
            
        cmd.extend([
            "-g", str(self.video_cfg.streaming.fps),
            "-f", "fifo",
            "-fifo_format", "mpegts",
            "-drop_pkts_on_overflow", "1",
            "-attempt_recovery", "1",
            "-recovery_wait_time", "1",
            "-format_opts", "flush_packets=1",
            self.video_cfg.streaming.url
        ])
        return cmd

    def build_command(self, output_file: Path) -> list[str]:
        """Public Command Builder tying the elements together."""
        cmd = ["ffmpeg", "-y", "-loglevel", self.log_level]

        if self.log_level != "debug":
            cmd.append("-nostats")
        
        inputs_cmd, audio_result = self._build_inputs()
        cmd.extend(inputs_cmd)
        
        filters_cmd, v_file, v_stream, a_stream = self._build_filters(audio_result)
        cmd.extend(filters_cmd)
        
        cmd.extend(self._build_file_output(output_file, v_file, audio_result))
        
        if self.video_cfg.streaming.enabled and v_stream:
            cmd.extend(self._build_stream_output(v_stream, a_stream))
            
        return cmd

    async def start(self, session_path: Path):
        """Common entry point to launch the FFmpeg process."""
        self.session_path = session_path
        self.timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        await self.pre_start()

        output_file = self.session_path / f"{self.name.lower()}recording__{self.timestamp}.mkv"
        cmd = self.build_command(output_file)
        
        logger.debug(f"[{self.name}] Command: {' '.join(cmd)}")
        
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        self._tasks = [
            asyncio.create_task(self._stream_logs()),
            asyncio.create_task(self._watch_process())
        ]
        logger.info(f"[{self.name}] Runner started (PID: {self.process.pid})")

    async def stop(self):
        """Gracefully shuts down the process and cleans up tasks."""
        self._stopping = True
        
        if self.process and self.process.returncode is None:
            try:
                logger.info(f"[{self.name}] Sending 'q' to gracefully stop FFmpeg...")
                self.process.stdin.write(b'q\n')
                await self.process.stdin.drain()
                
                await asyncio.wait_for(self.process.wait(), timeout=10.0)
                logger.info(f"[{self.name}] Stopped gracefully.")
            except (asyncio.TimeoutError, BrokenPipeError):
                logger.warning(f"[{self.name}] Force killing...")
                self.process.kill()
                await self.process.wait()

        for task in self._tasks:
            task.cancel()
        
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.post_stop()

        self.process = None
        self._stopping = False
        self._metadata_written = False
        self._tasks.clear()

    async def _stream_logs(self):
        if not self.process or not self.process.stderr:
            return
        try:
            while True:
                line_bytes = await self.process.stderr.readuntil((b"\n", b"\r"))
                line = line_bytes.decode('utf-8', errors='replace').strip()
                
                if line:
                    await self._process_log_line(line)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if not self._stopping:
                logger.error(f"[{self.name}] Log stream crashed: {e}")

    async def _process_log_line(self, line: str):
        # Extract UTC Sync Anchor
        if not self._metadata_written and "start:" in line and "Duration:" in line:
            if match := re.search(r"start:\s*([\d.]+)", line):
                start_epoch = float(match.group(1))
                await self._write_metadata(start_epoch)
                self._metadata_written = True

        # standard logging
        if not line.startswith("frame="):
            level = logging.ERROR if "error" in line.lower() or "fail" in line.lower() else logging.INFO
            logger.log(level, f"[{self.name}] {line}")

    async def _write_metadata(self, start_epoch: float):
        if not self.session_path:
            return
            
        start_ts = datetime.fromtimestamp(start_epoch, tz=timezone.utc)
        meta = {
            "start_epoch_sec": start_epoch,
            "start_utc_iso": start_ts.isoformat(),
            "runner": self.name,
            "fps": self.video_cfg.fps
        }
        
        meta_file = self.session_path / f"{self.name.lower()}recording_metadata__{self.timestamp}.json"
        
        with open(meta_file, "w") as f:
            json.dump(meta, f, indent=2)
        
        logger.info(f"[{self.name}] Metadata saved: {start_epoch}")

    async def _watch_process(self):
        if not self.process:
            return
        
        await self.process.wait()
        
        if not self._stopping:
            logger.error(f"[{self.name}] Process died unexpectedly (Exit: {self.process.returncode})")
            if self.on_error:
                asyncio.create_task(self.on_error())