FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg for RTSP streaming, libgl/glib for image processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU-first (keeps image lightweight on x86_64 / Intel N100), then requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    (pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu || pip install --no-cache-dir torch torchvision) && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download default vision transformer weights so container works offline
RUN python3 -c "from ultralytics import RTDETR; RTDETR('rtdetr-l.pt')"

# Copy application files
COPY server.py index.html style.css app.js ./

ENV PYTHONUNBUFFERED=1
ENV GATO_CONFIG=/app/config.json

EXPOSE 8000

CMD ["python3", "server.py", "8000"]
