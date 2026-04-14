from importlib.metadata import version
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, BaseModel
from pathlib import Path

class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"

class AudioConfig(BaseModel):
    enabled: bool = True
    device: str = "sysdefault:CARD=SoloCast"
    codec: str = "aac"
    bitrate: str = "128k"

class StreamingConfig(BaseModel):
    enabled: bool = False
    url: str = "udp://127.0.0.1:1234?pkt_size=1316"
    output_resolution: str = Field(default="1920x1080")
    fps: int = Field(default=30)
    bitrate: str = "4M"

class RecordingConfig(BaseModel):
    output_resolution: str = Field(default="3840x2160")
    fps: int = Field(default=30)
    video_bitrate: str = Field(default="15M")
    max_bitrate: str = Field(default="35M")

class AppSettings(BaseSettings):
    data_dir: Path = Field(default=Path("./data/"))
    nats_host: str = Field(default="nats://localhost:4222")

    # Screen capture
    display: str = Field(default=":0")
    mode: str = Field(default="gpu")

    # ffmpeg settings
    audio: AudioConfig = Field(default_factory=AudioConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    
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