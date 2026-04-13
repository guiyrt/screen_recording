import asyncio
import logging
import json
import nats

from .runner import Runner
from .configs import OrchestratedSettings
from .encoders import VideoEncoder

logger = logging.getLogger(__name__)

class ScreenManager:
    def __init__(self, settings: OrchestratedSettings, encoder: VideoEncoder, nc: nats.NATS):
        self.settings = settings
        self.encoder = encoder
        self.nc = nc

        self.runner: Runner | None = None
        self._heartbeat_task: asyncio.Task | None = None

    @property
    def is_recording(self) -> bool:
        return self.runner is not None and self.runner.is_recording
    
    async def _handle_runner_crash(self):
        """Callback fired by the Runner if FFmpeg dies unexpectedly."""
        logger.error("Runner emmited unexpected crash! Cleaning up.")
        
        # Clean up the dead runner
        await self.stop()
    
    async def start(self, sub_dir: str | None = None) -> None:
        """Creates and starts a fresh runner with new sinks."""
        if self.is_recording:
            raise RuntimeError("Screen recorder is already running.")
        
        session_path = (
            self.settings.data_dir / sub_dir / "video_recordings"
            if sub_dir is not None
            else self.settings.data_dir
        )
        session_path.mkdir(parents=True, exist_ok=True)
        
        self.runner = Runner(self.settings, self.encoder, on_error=self._handle_runner_crash)
        await self.runner.start(session_path)

    async def stop(self) -> None:
        """Cleans up the runner."""
        if self.runner:
            await self.runner.stop()
            self.runner = None
            logger.info("Screen recording runner stopped.")

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