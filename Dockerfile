FROM nvidia/cuda:12.8.1-base-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /app

# Install FFmpeg (and ca-certificates for downloading Python)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy 'uv' binary directly from Astral's image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# UV Configuration
ENV UV_COMPILE_BYTECODE=1 
ENV UV_LINK_MODE=copy 
ENV UV_NO_DEV=1

# Copy project definitions
COPY pyproject.toml uv.lock README.md ./

# Install dependencies
RUN uv sync --frozen --no-install-project --no-editable

# Copy source and install project
COPY src/ ./src
RUN uv sync --frozen --no-editable

# Activate the virtual environment for uv scripts
ENV PATH="/app/.venv/bin:$PATH"

# Environment variables for NVENC fallback
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,video,utility

CMD ["screen-record", "launch"]