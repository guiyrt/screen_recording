from importlib.metadata import version
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, BaseModel, PositiveInt
from pathlib import Path
from typing import Literal, Optional

class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"

class AudioSourceConfig(BaseModel):
    enabled: bool = False
    device: str = ""

class AudioConfig(BaseModel):
    microphone: AudioSourceConfig
    system: AudioSourceConfig
    codec: str = "libopus"
    bitrate: str = "128k"

class StreamingConfig(BaseModel):
    enabled: bool = False
    url: str = "udp://127.0.0.1:1234?pkt_size=1316"
    resolution: str = Field(default="1920:1080")
    fps: PositiveInt = Field(default=30)
    bitrate: str = "4M"

class VideoConfig(BaseModel):
    fps: PositiveInt = Field(default=30)
    resolution: Optional[str] = None
    video_bitrate: str = Field(default="15M")
    max_bitrate: str = Field(default="35M")
    cq: int = Field(default=22, description="Constant Quality target (lower is better quality)")
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)

class GoProConfig(VideoConfig):
    enabled: bool = False
    resolution: Literal["1080", "720", "480"] = "1080"
    serial_number: str = ""
    fov: Literal["Wide", "Narrow", "Superview", "Linear"] = "Wide"
    port: PositiveInt = 8554
    cq: int = Field(default=26) # Slightly higher CQ to prevent file bloat; bitrate handles the rest
    record_native_audio: bool = Field(default=True)

class ScreenConfig(VideoConfig):
    enabled: bool = False
    display: str = Field(default=":0")
    cq: int = Field(default=18) # High quality baseline for razor-crisp ATC UI text

class AppSettings(BaseSettings):
    data_dir: Path = Field(default=Path("./data/"))
    nats_host: str = Field(default="nats://localhost:4222")

    # Screen capture
    mode: str = Field(default="gpu")

    # ffmpeg settings
    audio: AudioConfig = Field(default_factory=AudioConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    gopro: GoProConfig = Field(default_factory=GoProConfig)
    
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    __version__: str = version("screen_recorder")

    model_config = SettingsConfigDict(
        env_prefix="SCREEN__",
        env_file=".env",
        env_nested_delimiter='__',
        case_sensitive=False
    )

class OrchestratedSettings(AppSettings):
    health_subject: str = "screen.health"
    cmds_subject: str = "screen.cmds"