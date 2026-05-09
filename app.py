import os
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import requests as http_requests

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["https://kamsnc.netlify.app", "http://localhost:5173"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
API_SECRET = os.getenv("API_SECRET", "change-me")


# ============ HEALTH CHECK ============
@app.route("/")
def health():
    return jsonify({"status": "ok", "service": "kamapp-backend"})


# ============ AI ASSISTANT PROXY ============
@app.route("/api/assistant", methods=["POST"])
def assistant_proxy():
    """Proxy para la Claude API. Recibe mensajes del frontend y los envía a Claude."""
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY no configurada"}), 500

    data = request.get_json()
    if not data or "messages" not in data:
        return jsonify({"error": "Falta el campo 'messages'"}), 400

    system_prompt = data.get("system", "")
    messages = data.get("messages", [])

    try:
        response = http_requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": system_prompt,
                "messages": messages,
            },
            timeout=30,
        )

        if response.status_code != 200:
            logger.error(f"Claude API error: {response.status_code} - {response.text}")
            return jsonify({"error": f"Claude API error: {response.status_code}"}), 502

        return jsonify(response.json())

    except http_requests.Timeout:
        return jsonify({"error": "Timeout llamando a Claude API"}), 504
    except Exception as e:
        logger.error(f"Error en assistant proxy: {e}")
        return jsonify({"error": str(e)}), 500


# ============ RUN ============
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
