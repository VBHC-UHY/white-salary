FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata/source and install dependencies declared in pyproject.
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[memory-vector]"

# Copy runtime entry/config templates
COPY run_server.py .
COPY conf.default.yaml .
COPY prompts/ prompts/

ENV PYTHONPATH=src

EXPOSE 12400

CMD ["python", "run_server.py"]
