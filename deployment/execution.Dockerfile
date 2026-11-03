FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir matplotlib==3.10.8 numpy==2.2.6 pillow==11.3.0
ENV MPLCONFIGDIR=/tmp/matplotlib PYTHONIOENCODING=utf-8
USER 65534:65534
WORKDIR /output
