FROM python:3.11-slim

# fonts-dejavu-core provides the default FONT_PATH used by video_engine.py.
# ffmpeg is not required as a system package: imageio-ffmpeg (a MoviePy
# dependency) ships its own static ffmpeg binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/generated_videos
ENV OUTPUT_DIR=/data/generated_videos
VOLUME ["/data"]

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
