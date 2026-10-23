FROM python:3.12-slim
WORKDIR /app
COPY deployment/requirements.server.lock.txt /app/requirements.server.lock.txt
RUN ["python","-m","pip","install","--no-cache-dir","-r","/app/requirements.server.lock.txt"]
COPY backend /app/backend
COPY runner.py /app/runner.py
COPY prompts /app/prompts
COPY frontend/src/collaboration.html /app/frontend/src/collaboration.html
RUN ["python","-c","import os; os.makedirs('/data',exist_ok=True); os.chown('/data',10001,10001); os.chmod('/data',0o700)"]
ENV PAPER_DATA_DIR=/data PAPER_COLLAB_BIND=0.0.0.0 PAPER_COLLAB_PORT=8774 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8774
ENTRYPOINT ["python","-I","-X","utf8","/app/runner.py"]
CMD ["collaboration-server"]
