import asyncio
import logging
import signal
import nats
import typer

from .configs import AppSettings, OrchestratedSettings, LoggingConfig
from .manager import ScreenManager
from .runner import Runner
from .ffmpeg import get_encoder_strategy

app = typer.Typer(no_args_is_help=True, add_completion=False)

def get_logger(settings: LoggingConfig) -> logging.Logger:
    logging.basicConfig(
        level=settings.level,
        format=settings.format
    )
    
    return logging.getLogger(__name__)

def setup_signals(stop_event: asyncio.Event):
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

async def setup_nats(host: str) -> nats.NATS:
    nc = nats.NATS()
    while True:
        try:
            await nc.connect(host, allow_reconnect=True, max_reconnect_attempts=-1)
            logging.info("NATS Connected.")
            return nc
        except Exception as e:
            logging.error(f"NATS connection failed: {e}. Retrying...")
            await asyncio.sleep(5)

@app.command()
def serve():
    settings = AppSettings()
    logger = get_logger(settings.logging)
    logger.debug(settings)
    
    async def _run():
        stop_event = asyncio.Event()
        setup_signals(stop_event)

        async def _on_error():
            logger.error("Standalone Runner crashed. Setting stop event.")
            stop_event.set()
        
        encoder_strategy = await get_encoder_strategy(settings)
        runner = Runner(settings, encoder_strategy, on_error=_on_error)
        
        await runner.start()
        
        try:
            logger.info("Running screen recording. Press Ctrl+C or stop container to exit.")
            await stop_event.wait()
        finally:
            logger.info("Shutting down screen recording...")
            await runner.stop()

    try:
        asyncio.run(_run())
        logger.info("Shutdown complete.")
    except Exception as e:
        logger.critical(f"System failure: {e}", exc_info=True)
        raise typer.Exit(1)

@app.command()
def launch():
    settings = OrchestratedSettings()
    logger = get_logger(settings.logging)
    logger.debug(settings)
    
    async def _run():
        stop_event = asyncio.Event()
        setup_signals(stop_event)
        
        nc = await setup_nats(settings.nats_host)
        encoder_strategy = await get_encoder_strategy(settings)
        manager = ScreenManager(settings, encoder_strategy, nc)
        
        try:
            # Pass stop_event to manager so it knows when to gracefully exit
            await manager.listen_to_nats(stop_event)
        finally:
            logger.info("Draining NATS connection...")
            await nc.drain()

    try:
        asyncio.run(_run())
        logger.info("Shutdown complete.")
    except Exception as e:
        logger.critical(f"System failure: {e}", exc_info=True)
        raise typer.Exit(1)

if __name__ == "__main__":
    app()