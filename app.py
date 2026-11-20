
import os

from dotenv import load_dotenv
from flask import Flask, jsonify
from werkzeug.exceptions import RequestEntityTooLarge
from flask_cors import CORS

# Load environment variables before importing routes.
load_dotenv()

from routes.analysis import analysis_bp


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["MAX_FORM_MEMORY_SIZE"] = 100 * 1024 * 1024

# Allow React frontend to communicate with Flask.
CORS(
    app,
    supports_credentials=True,
    origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173"
    ]
)

app.config["MONGODB_URI"] = os.getenv("MONGODB_URI", "")


# Register API routes.
app.register_blueprint(analysis_bp)
if app.config["MONGODB_URI"] and "<cluster>" not in app.config["MONGODB_URI"]:
    from routes.auth import auth_bp

    app.register_blueprint(auth_bp)


# Health check
@app.get("/api/health")
def health():
    return jsonify(status="ok")


@app.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    return jsonify(
        error="Project upload is too large. Upload no more than 100 MB and avoid dependency folders."
    ), 413


if __name__ == "__main__":

    print("Starting BugRadar backend...")

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
