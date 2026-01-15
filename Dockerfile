# Dockerfile (Django + static incluidos)
# Build:
#   docker build -t evo_pvp .
# Run (dev rápido):
#   docker run --rm -p 8000:8000 -e DJANGO_SECRET_KEY="devsecret" evo_pvp
#
# Run (prod recomendado con gunicorn):
#   docker run --rm -p 8000:8000 -e DJANGO_SECRET_KEY="prodsecret" -e DJANGO_DEBUG="0" evo_pvp

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# deps de sistema mínimos (sqlite no requiere extras)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

# Copiar proyecto
COPY . /app

# Variables por defecto
# (DJANGO_SECRET_KEY es obligatoria en settings; DJANGO_DEBUG opcional)
ENV DJANGO_DEBUG=0

# Collectstatic (si tu settings STATIC_ROOT está configurado)
# Si no lo tienes, igual no rompe si usas WhiteNoise con STATICFILES_STORAGE.
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

# Si no tienes gunicorn en requirements.txt, agrega: gunicorn==21.2.0
# y en settings usa WhiteNoise para servir /static en prod.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "--timeout", "120"]
