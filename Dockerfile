FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy scripts
COPY auto_sync.py .
COPY sync_gear.py .
COPY sync_stryd.py .

# State file and Garmin token cache are mounted as volumes at runtime
# /app/data/auto_sync_state.json  -- persists Strava/Stryd tokens + last sync epoch
# /app/garminconnect/             -- persists Garmin session tokens

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Default: dry run. Override CMD to pass --apply.
CMD ["python", "-I", "auto_sync.py"]
