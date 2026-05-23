FROM python:3.9-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"
ENV PORT=7860

WORKDIR /app

COPY --chown=user discord-bot/ /app/
COPY --chown=user requirements.txt /app/

RUN pip install --no-cache-dir --upgrade -r requirements.txt

CMD ["python", "bot.py"]
