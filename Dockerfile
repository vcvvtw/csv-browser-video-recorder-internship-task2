FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2 \
    libapache2-mod-wsgi-py3 \
    python3 \
    python3-pip \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY . /app/

RUN cp /app/apache.conf /etc/apache2/sites-available/000-default.conf \
    && a2enmod wsgi headers \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /app/uploads \
    && chown www-data:www-data /app/uploads \
    && chmod 775 /app/uploads \
    && ln -sf /dev/stdout /var/log/apache2/access.log \
    && ln -sf /dev/stderr /var/log/apache2/error.log

EXPOSE 80

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["apachectl", "-D", "FOREGROUND"]
