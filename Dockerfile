FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Instalar dependencias
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar proyecto
COPY . .

# Collectstatic (debe funcionar, si falla que el build falle)
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Render inyecta PORT a veces, pero tú ya estás usando 8000 y Render lo detecta.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120"]
