# Use an official Python runtime as a parent image
FROM python:3.11-slim-bullseye

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Arguments for user and group IDs
ARG UID=1000
ARG GID=1000

# Create a non-root user and group with specified IDs
RUN groupadd -g ${GID} -r appgroup && useradd --no-log-init -u ${UID} -g ${GID} -r appuser

# Install system dependencies required by rt-utils or other libraries
# libglib2.0-0, libsm6, libxrender1, libxext6, libgl1 are common for image processing/GUI libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgl1 \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create default directories for logs and working data and set ownership
# These paths should match the defaults in config_loader.py if config is not mounted
# Or, they serve as mount points if volumes are used.
RUN mkdir -p /home/appuser/CORRECT_logs /home/appuser/CORRECT_working /app/config /mnt/shared && \
    chown -R appuser:appgroup /home/appuser /home/appuser/CORRECT_logs /home/appuser/CORRECT_working /app /mnt/shared

# Copy and register the entrypoint script (runs as root to fix volume ownership on startup)
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Define mount points for persistent data and configuration
VOLUME ["/home/appuser/CORRECT_logs", "/home/appuser/CORRECT_working", "/app/config", "/mnt/shared"]

# Stay as root so the entrypoint can chown mounted volumes, then drop to appuser via gosu
ENTRYPOINT ["docker-entrypoint.sh"]

# Define the command to run the application
# It expects config.yaml to be in /app/config/config.yaml
CMD ["python", "main.py", "--config", "/app/config/config.yaml"]