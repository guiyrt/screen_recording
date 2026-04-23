import asyncio
import logging
import json
from datetime import datetime, timezone
import nats

from .configs import OrchestratedSettings
from .encoders import VideoEncoder
from .runners import BaseRunner, ScreenRunner, GoProRunner

logger = logging.getLogger(__name__)

class ScreenManager:
    _runner_cls: type[BaseRunner]

    def __init__(self, settings: OrchestratedSettings, encoder: VideoEncoder, nc: nats.NATS | None = None):
        self.settings = settings
        self.encoder = encoder
        self.nc = nc

        self.runner: BaseRunner | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._lock = asyncio.Lock() # Prevent concurrent start/stop commands

        # Get which runner to use
        if sum(runner.enabled for runner in (settings.screen, settings.gopro)) > 1:
            raise ValueError("More than one video source enabled, please select only one.")
        if settings.screen.enabled:
            self._runner_cls = ScreenRunner
        elif settings.gopro.enabled:
            self._runner_cls = GoProRunner
        else:
            raise ValueError("No video source enabled. Please enable ´screen´ or ´gopro´.")

    @property
    def is_recording(self) -> bool:
        return self.runner is not None

    async def _handle_runner_error(self):
        """Callback triggered if a runner crashes unexpectedly."""
        logger.error("A runner has emitted a crash signal! Initiating stop.")
        await self.stop()

    async def start(self, sub_dir: str | None = None) -> None:
        """Factory: Instantiates and starts all enabled runners."""
        async with self._lock:
            if self.is_recording:
                logger.warning("Start command ignored: Already recording.")
                return

            # Setup Session Directory & Timestamp
            session_path = (
                self.settings.data_dir / sub_dir / "videoRecordings"
                if sub_dir is not None
                else self.settings.data_dir
            )
            session_path.mkdir(parents=True, exist_ok=True)


            self.runner = self._runner_cls(
                video_cfg=self.settings.screen if self._runner_cls is ScreenRunner else self.settings.gopro,
                audio_cfg=self.settings.audio,
                encoder=self.encoder,
                on_error=self._handle_runner_error,
                log_level=self.settings.logging.level
            )

            # Start runner
            try:
                await self.runner.start(session_path)
                logger.info(f"{self._runner_cls.__name__} started.")
            except Exception as e:
                logger.error(f"Failed to launch {self._runner_cls.__name__}: {e}")
                await self.stop()
                raise

    async def stop(self) -> None:
        """Stops all active runners."""
        async with self._lock:
            if not self.runner:
                return

            try:
                logger.info(f"Stopping {self._runner_cls.__name__}...")
                await self.runner.stop()
            finally:
                self.runner = None
                logger.info(f"{self._runner_cls.__name__} stopped.")

    async def listen_to_nats(self, stop_event: asyncio.Event):
        """Orchestration loop: Heartbeats + Commands."""
        
        async def heartbeat():
            try:
                while not stop_event.is_set():
                    if self.nc.is_connected:
                        await self.nc.publish(
                            self.settings.health_subject,
                            json.dumps({"is_recording": self.is_recording}).encode()
                        )
                    
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Heartbeat task crashed: {e}")

        async def cmd_handler(msg):
            try:
                data = json.loads(msg.data.decode())
                cmd = data.get("cmd")
                
                if cmd == "start":
                    await self.start(data.get("session_id", None))
                    await msg.respond(json.dumps({"status": "ok"}).encode())
                
                elif cmd == "stop":
                    await self.stop()
                    await msg.respond(json.dumps({"status": "ok"}).encode())
                
                else:
                    await msg.respond(json.dumps({"status": "error", "error": f"Unknown cmd: {cmd}"}).encode())
            
            except Exception as e:
                logger.error(f"Command execution failed: {e}")
                await msg.respond(json.dumps({"status": "error", "error": str(e)}).encode())

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(heartbeat())
        
        # Subscribe to Commands
        sub = await self.nc.subscribe(self.settings.cmds_subject, cb=cmd_handler)
        logger.info("STANDBY: listening for NATS commands...")
        
        try:
            # Block until the main app signals shutdown
            await stop_event.wait()
        finally:
            logger.info("Tearing down Orchestrator listener...")
            await sub.unsubscribe()
            
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            
            await self.stop() # Ensure runner stops if container is killed mid-recording