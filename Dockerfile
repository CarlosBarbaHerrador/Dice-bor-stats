FROM python:3.11

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

COPY --chown=user discord-bot/ /app/
COPY --chown=user requirements.txt /app/

RUN pip install --no-cache-dir --upgrade -r requirements.txt

CMD ["python", "bot.py"]
