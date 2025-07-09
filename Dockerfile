# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies for Pillow, SQLite, etc.
RUN apt-get update && apt-get install -y \
    build-essential \
    libsqlite3-dev \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Expose port for Flask keep-alive
EXPOSE 8080

# Run the bot
CMD ["python", "main.py"]
