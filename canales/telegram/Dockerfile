FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV DATA_DIR=/data
# Sin EXPOSE: el canal no publica ningún puerto. Es él quien llama a Telegram
# (long polling) y al core; desde fuera no se le puede llamar.
CMD ["python", "-m", "app.main"]
