FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg for RTSP streaming, libgl/glib for image processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

# Install PyTorch (default: CPU-only for lightweight images on Intel N100; set TORCH_INDEX=cuda for NVIDIA GPUs)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    if [ "$TORCH_INDEX" = "cuda" ]; then \
        pip install --no-cache-dir torch torchvision; \
    else \
        (pip install --no-cache-dir torch torchvision --index-url "$TORCH_INDEX" || pip install --no-cache-dir torch torchvision); \
    fi && \
    pip install --no-cache-dir -r requirements.txt
# Pre-download default weights (yolo11s.pt for CPU / Intel N100, rtdetr-l.pt for GPU) so container works offline
RUN python3 -c "from ultralytics import YOLO, RTDETR; YOLO('yolo11s.pt'); RTDETR('rtdetr-l.pt')"

# Copy application files
COPY server.py index.html style.css app.js ./

ENV PYTHONUNBUFFERED=1
ENV GATO_CONFIG=/app/config.json

EXPOSE 8000

CMD ["python3", "server.py", "8000"]
