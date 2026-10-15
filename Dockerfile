FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . /app/

# Expose port (used by web app)
EXPOSE 8000

# Default command (overridden in docker-compose)
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "matchdex_web.wsgi:application"]
