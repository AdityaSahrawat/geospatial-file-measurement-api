FROM python:3.13-slim

WORKDIR /app

# Install system dependencies required by GeoPandas / Fiona / GDAL
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    gdal-bin \
    libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency installation
RUN pip install uv

# Copy dependency files first (layer caching)
COPY pyproject.toml uv.lock ./

# Install project and all its dependencies from pyproject.toml
RUN uv pip install --system --no-cache .

# Copy application source
COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
