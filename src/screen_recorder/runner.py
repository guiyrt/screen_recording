from typing import Callable
import asyncio
import logging
import signal
from datetime import datetime, timezone
from pathlib import Path

from .configs import AppSettings
from .encoders import VideoEncoder
from .ffmpeg import build_command

logger = logging.getLogger(__name__)

class Runner:
    def __init__(self, settings: AppSettings, encoder: VideoEncoder, on_error: Callable[[], None] | None = None):
        self.settings = settings
        self.encoder = encoder
        self.on_error = on_error

        self.process: asyncio.subprocess.Process | None = None
        self._stopping: bool = False
        self._log_task: asyncio.Task | None = None
        self._wait_task: asyncio.Task | None = None

    @property
    def is_recording(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _stream_ffmpeg_logs(self):
        """Continuously reads FFmpeg's stderr and pipes it to the Python logger."""
        if not self.process or not self.process.stderr:
            return
            
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break # EOF reached
                    
                # FFmpeg outputs a lot of text. 
                # We decode it, strip the newline, and log it.
                decoded_line = line.decode('utf-8', errors='replace').strip()
                
                # FFmpeg prints normal info to stderr as well, 
                # but if it contains "Error" we can highlight it.
                if "Error" in decoded_line or "fail" in decoded_line.lower():
                    logger.error(f"[FFMPEG] {decoded_line}")
                else:
                    logger.info(f"[FFMPEG] {decoded_line}")
        except asyncio.CancelledError:
            pass
    
    async def _watch_process(self):
        """Waits for the process to exit. If it exits unexpectedly, clean up."""
        if not self.process:
            return
            
        # Wait for the subprocess to finish naturally or crash
        await self.process.wait()
        
        # If it crashed (we didn't explicitly call stop())
        if not self._stopping:
            logger.error(f"FFmpeg exited prematurely! (Exit code: {self.process.returncode})")
            
            if self.on_error is not None:
                await self.on_error()
    
    async def start(self, output_dir: Path | None = None) -> None:
        """Creates and starts a fresh runner with new sinks."""
        if self.is_recording:
            raise RuntimeError("Screen recorder is already running.")
        
        session_path = output_dir or self.settings.data_dir
        session_path.mkdir(parents=True, exist_ok=True)
        
        # Unique file per start to prevent overwrites
        output_file = session_path / f"screen_recording__{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.mkv"
        
        ffmpeg_cmd = build_command(self.settings, self.encoder, output_file)
        logger.debug(f"FFmpeg Command: {' '.join(ffmpeg_cmd)}")

        # Launch Subprocess
        self.process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL, 
            stderr=asyncio.subprocess.PIPE
        )

        self._log_task = asyncio.create_task(self._stream_ffmpeg_logs())
        self._wait_task = asyncio.create_task(self._watch_process())

        logger.info(f"FFmpeg started (PID: {self.process.pid}), saving to {session_path}")

    async def stop(self) -> None:
        if self.process is None:
            return

        logger.info("Stopping Screen Recording... flushing video buffers.")
        self._stopping = True

        if self.process.returncode is None:
            try:
                self.process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info("Screen Recording stopped successfully.")
            except asyncio.TimeoutError:
                logger.warning("FFmpeg did not stop in time! Forcing KILL.")
                self.process.kill()
                await self.process.wait()
        
        tasks: list[asyncio.Task] = []
        if self._log_task is not None:
            tasks.append(self._log_task)
        if self._wait_task is not None:
            tasks.append(self._wait_task)

        for task in tasks:
            task.cancel()
        
        await asyncio.gather(*tasks)

        self.process = None
        self._stopping = False
