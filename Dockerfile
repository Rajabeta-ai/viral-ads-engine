# Render's native Python runtime has no ffmpeg, and this pipeline cannot run without it
# (engine/edit.py shells out to ffmpeg/ffprobe for every concat and the outro append).
# So we ship a container instead of using the native runtime.
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# DATA_DIR points at the Render persistent disk mount; the app falls back to ./data locally.
ENV DATA_DIR=/var/data \
    PORT=8765 \
    WINDOW_DAYS=3 \
    PYTHONUNBUFFERED=1

EXPOSE 8765
CMD ["python", "-u", "ui/server.py"]
