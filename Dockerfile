FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn loguru pydantic pyyaml aiohttp chromadb

# Copy source code
COPY src/ src/
COPY run_server.py .
COPY conf.default.yaml .
COPY prompts/ prompts/

ENV PYTHONPATH=src

EXPOSE 12400

CMD ["python", "run_server.py"]
