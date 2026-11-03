ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
ARG TORCH_CHANNEL=cpu
RUN python -m pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/${TORCH_CHANNEL}
RUN python -m pip install --no-cache-dir surya-ocr==0.17.1 fastapi uvicorn python-multipart
WORKDIR /app
COPY services/surya_service.py ./surya_service.py
ENV SURYA_DATA_ROOT=/data/surya HF_HOME=/data/model-cache
EXPOSE 8769
CMD ["uvicorn","surya_service:app","--host","0.0.0.0","--port","8769"]
