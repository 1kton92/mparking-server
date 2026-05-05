FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema para Playwright
RUN apt-get update && apt-get install -y \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar navegadores de Playwright
RUN playwright install

COPY . .

EXPOSE 8000

CMD ["python", "run.py"]