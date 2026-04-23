import asyncio
import logging
import signal
import nats
from nats.errors import NoServersError
import typer

from .configs import AppSettings, OrchestratedSettings, LoggingConfig
from .manager import ScreenManager
from .runners import BaseRunner
from .factories import get_encoder_strategy

app = typer.Typer(no_args_is_help=True, add_completion=False)

logger = logging.getLogger(__name__)

def setup_logger(settings: LoggingConfig) -> logging.Logger:
    logging.getLogger("nats").setLevel(logging.ERROR)
    logging.getLogger("nats.aio.client").setLevel(logging.CRITICAL)
    logging.basicConfig(level=settings.level, format=settings.format)

def setup_signals(stop_event: asyncio.Event):
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

async def setup_nats(host: str) -> nats.NATS:
    """
    Initializes NATS with custom logging to prevent traceback spam.
    """
    nc = nats.NATS()

    async def disconnected_cb():
        logger.warning("NATS: Connection disconnected.")

    async def reconnected_cb():
        logger.info(f"NATS: Connection restored to {nc.connected_url.netloc}")

    async def error_cb(e):
        # Ignore common network noise during background reconnect attempts
        if isinstance(e, (asyncio.TimeoutError, ConnectionRefusedError, OSError)):
            return

        err_msg = str(e).strip()

        # Some NATS specific EOF/disconnect errors might bypass the instance check
        if "empty response from server" in err_msg or "UnexpectedEOF" in err_msg:
            return

        # If it's an error with an empty string, log its class name instead
        if not err_msg:
            err_msg = type(e).__name__
            
        logger.error(f"NATS Internal Error: {err_msg}")

    async def closed_cb():
        logger.info("NATS: Connection closed.")

    # Connection Loop
    while True:
        try:
            await nc.connect(
                host,
                allow_reconnect=True,
                max_reconnect_attempts=-1, # Infinite reconnection
                reconnect_time_wait=2, # Wait 2s between attempts
                disconnected_cb=disconnected_cb,
                reconnected_cb=reconnected_cb,
                error_cb=error_cb,
                closed_cb=closed_cb,
            )
            logger.info(f"NATS: Initial connection established to {host}")
            return nc
        except (asyncio.TimeoutError, NoServersError, OSError) as e:
            logger.warning(f"NATS: Waiting for server at {host}... ({e})")
            await asyncio.sleep(5)

@app.command()
def serve():
    """Standalone mode: Starts recording immediately and runs until interrupted."""
    settings = AppSettings()
    setup_logger(settings.logging)
    
    async def _run():
        stop_event = asyncio.Event()
        setup_signals(stop_event)

        # In standalone mode, if any runner crashes, we shut down the whole app
        async def _on_error():
            logger.error("A runner crashed in standalone mode. Shutting down...")
            stop_event.set()
        
        encoder_strategy = await get_encoder_strategy(settings)
        
        # Instantiate Manager WITHOUT a NATS client
        manager = ScreenManager(settings, encoder_strategy, nc=None)
        
        # Override the default error handler to stop the main event loop
        manager._handle_runner_error = _on_error
        
        try:
            # Start all enabled runners
            await manager.start()
            
            logger.info("Recording active. Press Ctrl+C to stop.")
            await stop_event.wait()
        finally:
            logger.info("Shutting down runners...")
            await manager.stop()

    try:
        asyncio.run(_run())
        logger.info("Shutdown complete.")
    except Exception as e:
        logger.critical(f"System failure: {e}", exc_info=True)
        raise typer.Exit(1)

@app.command()
def launch():
    settings = OrchestratedSettings()
    setup_logger(settings.logging)
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