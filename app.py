import os
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import requests as http_requests
import db
from reports import generate_weekly_report, send_report_email
from alerts import run_alert_generation

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["https://kamsnc.netlify.app", "http://localhost:5173"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
API_SECRET = os.getenv("API_SECRET", "change-me")


@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "kamapp-backend",
        "features": {
            "assistant": bool(ANTHROPIC_API_KEY),
            "reports": db.is_configured(),
        }
    })


# ============ AI ASSISTANT ============
@app.route("/api/assistant", methods=["POST"])
def assistant_proxy():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY no configurada"}), 500

    data = request.get_json()
    if not data or "messages" not in data:
        return jsonify({"error": "Falta 'messages'"}), 400

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
                "system": data.get("system", ""),
                "messages": data.get("messages", []),
            },
            timeout=30,
        )
        if response.status_code != 200:
            return jsonify({"error": f"Claude API: {response.status_code}"}), 502
        return jsonify(response.json())
    except http_requests.Timeout:
        return jsonify({"error": "Timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============ REPORTS ============
@app.route("/api/reports/weekly", methods=["POST"])
def trigger_weekly_report():
    if not db.is_configured():
        return jsonify({"error": "Supabase no configurado"}), 500
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if key != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        results = run_weekly_reports()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Error reporte: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports/preview/<manager_id>")
def preview_report(manager_id):
    if not db.is_configured():
        return jsonify({"error": "Supabase no configurado"}), 500
    try:
        report = generate_weekly_report(manager_id)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/generate", methods=["POST"])
def trigger_alerts():
    if not db.is_configured():
        return jsonify({"error": "Supabase no configurado"}), 500
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if key != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        results = run_alert_generation()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_weekly_reports():
    results = []
    managers = db.query("profiles", filters={
        "role": {"in": "(coordinator,manager,director)"},
        "is_active": True,
    })

    for manager in managers:
        try:
            report = generate_weekly_report(manager["id"])
            if manager.get("email"):
                send_report_email(manager["email"], manager["full_name"], report)
                results.append({"manager": manager["full_name"], "status": "sent"})
            else:
                results.append({"manager": manager["full_name"], "status": "skipped"})
        except Exception as e:
            results.append({"manager": manager["full_name"], "status": "error", "error": str(e)})

    return results


# ============ SCHEDULER ============
if db.is_configured():
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_weekly_reports, "cron", day_of_week="mon", hour=5, minute=0, id="weekly_report", replace_existing=True)
    scheduler.add_job(run_alert_generation, "cron", hour=4, minute=0, id="daily_alerts", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler activo: reportes lun 7AM ES, alertas diario 6AM ES")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
