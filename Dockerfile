FROM mcr.microsoft.com/playwright:v1.40.0-focal

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar navegadores de Playwright
RUN playwright install

COPY . .

EXPOSE 8000

CMD ["python", "run.py"]