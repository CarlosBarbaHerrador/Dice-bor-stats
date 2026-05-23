from flask import Flask
from threading import Thread

app = Flask(__name__)


@app.route("/")
def home():
    return "Estoy vivo"


def keep_alive():
    thread = Thread(target=lambda: app.run(host="0.0.0.0", port=8082))
    thread.daemon = True
    thread.start()
