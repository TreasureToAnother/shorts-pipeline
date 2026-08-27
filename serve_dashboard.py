"""
Local dashboard server.
Run: python serve_dashboard.py
Then open http://localhost:5050 while the orchestrator runs elsewhere
(separate terminal / separate GitHub Actions run) to watch the
analytics and queue update live.
"""
import os
from flask import Flask, send_from_directory

app = Flask(__name__)
ROOT = os.path.dirname(__file__)


@app.route("/")
def index():
    return send_from_directory(os.path.join(ROOT, "dashboard"), "index.html")


@app.route("/<string:filename>.json")
def data_file(filename):
    return send_from_directory(os.path.join(ROOT, "data"), f"{filename}.json")


@app.route("/<path:filename>")
def dashboard_asset(filename):
    """Static files that live alongside index.html — e.g. the favicon."""
    return send_from_directory(os.path.join(ROOT, "dashboard"), filename)


if __name__ == "__main__":
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    if not os.path.exists(os.path.join(ROOT, "data", "status.json")):
        from status_store import reset
        reset()
    app.run(port=5050, debug=False)
