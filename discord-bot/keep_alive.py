import os
from flask import Flask
from threading import Thread

app = Flask(__name__)


@app.route("/")
def home():
    return "Estoy vivo"


def keep_alive():
    port = int(os.environ.get("PORT", 7860))
    thread = Thread(target=lambda: app.run(host="0.0.0.0", port=port))
    thread.daemon = True
    thread.start()
