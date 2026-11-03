FROM node:24-bookworm-slim AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend --ignore-scripts --no-audit --no-fund
COPY frontend ./frontend
COPY scripts/prepare-studio-assets.mjs ./scripts/prepare-studio-assets.mjs
RUN npm run build --prefix frontend

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng libreoffice-writer fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY deployment/requirements.workbench.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY prompts ./prompts
COPY runner.py ./runner.py
COPY services ./services
COPY scripts/yantai_data_offline.py ./scripts/yantai_data_offline.py
COPY scripts/yantai_query_projection.py ./scripts/yantai_query_projection.py
COPY --from=frontend /app/frontend/dist ./frontend/dist
RUN useradd --uid 10001 --create-home workbench && mkdir -p /data /app/.runtime && chown workbench:workbench /data /app/.runtime
ENV PAPER_DATA_DIR=/data PAPER_PORT=8765 PAPER_BIND=0.0.0.0 PAPER_CONTAINER_DEPLOYMENT=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
USER 10001:10001
EXPOSE 8765
ENTRYPOINT ["python","-I","-X","utf8","/app/runner.py"]
CMD ["server"]
