import os
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import requests as http_requests
from apscheduler.schedulers.background import BackgroundScheduler
from reports import generate_weekly_report, send_report_email
from alerts import run_alert_generation
from supabase import create_client

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=["https://kamsnc.netlify.app", "http://localhost:5173"])

# Config
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
API_SECRET = os.getenv("API_SECRET", "change-me")

# Supabase client (service role)
supabase = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    logger.info("Supabase conectado")
else:
    logger.warning("SUPABASE_URL o SUPABASE_SERVICE_KEY no configuradas - reportes desactivados")


# ============ HEALTH CHECK ============
@app.route("/")
def health():
    return jsonify({
        "status": "ok",
        "service": "kamapp-backend",
        "features": {
            "assistant": bool(ANTHROPIC_API_KEY),
            "reports": bool(supabase),
        }
    })


# ============ AI ASSISTANT PROXY ============
@app.route("/api/assistant", methods=["POST"])
def assistant_proxy():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY no configurada"}), 500

    data = request.get_json()
    if not data or "messages" not in data:
        return jsonify({"error": "Falta el campo 'messages'"}), 400

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
            logger.error(f"Claude API error: {response.status_code} - {response.text}")
            return jsonify({"error": f"Claude API error: {response.status_code}"}), 502

        return jsonify(response.json())

    except http_requests.Timeout:
        return jsonify({"error": "Timeout llamando a Claude API"}), 504
    except Exception as e:
        logger.error(f"Error en assistant proxy: {e}")
        return jsonify({"error": str(e)}), 500


# ============ REPORTES ============
@app.route("/api/reports/weekly", methods=["POST"])
def trigger_weekly_report():
    if not supabase:
        return jsonify({"error": "Supabase no configurado"}), 500

    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if key != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        results = run_weekly_reports()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Error en reporte semanal: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/reports/preview/<manager_id>", methods=["GET"])
def preview_report(manager_id):
    if not supabase:
        return jsonify({"error": "Supabase no configurado"}), 500

    try:
        report = generate_weekly_report(supabase, manager_id)
        return jsonify(report)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alerts/generate", methods=["POST"])
def trigger_alerts():
    if not supabase:
        return jsonify({"error": "Supabase no configurado"}), 500

    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if key != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        results = run_alert_generation(supabase)
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Error generando alertas: {e}")
        return jsonify({"error": str(e)}), 500


# ============ LÓGICA DE REPORTES ============
def run_weekly_reports():
    results = []

    response = supabase.table("profiles").select("*").in_(
        "role", ["coordinator", "manager", "director"]
    ).eq("is_active", True).execute()

    managers = response.data or []
    logger.info(f"Generando reportes para {len(managers)} managers")

    for manager in managers:
        try:
            report = generate_weekly_report(supabase, manager["id"])

            if manager.get("email"):
                send_report_email(
                    to_email=manager["email"],
                    to_name=manager["full_name"],
                    report=report,
                )
                results.append({
                    "manager": manager["full_name"],
                    "email": manager["email"],
                    "status": "sent",
                    "kams": len(report.get("kams", [])),
                })
            else:
                results.append({
                    "manager": manager["full_name"],
                    "status": "skipped_no_email",
                })
        except Exception as e:
            logger.error(f"Error reporte para {manager['full_name']}: {e}")
            results.append({
                "manager": manager["full_name"],
                "status": "error",
                "error": str(e),
            })

    return results


# ============ SCHEDULER (CRON) ============
scheduler = BackgroundScheduler()

if supabase:
    # Reporte semanal: lunes a las 7:00 AM (UTC+2 España = 5:00 UTC)
    scheduler.add_job(
        run_weekly_reports,
        "cron",
        day_of_week="mon",
        hour=5,
        minute=0,
        id="weekly_report",
        replace_existing=True,
    )

    # Generación de alertas: todos los días a las 6:00 AM (4:00 UTC)
    scheduler.add_job(
        lambda: run_alert_generation(supabase),
        "cron",
        hour=4,
        minute=0,
        id="daily_alerts",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler iniciado: reportes (lunes 7AM ES) + alertas (diario 6AM ES)")
else:
    logger.warning("Scheduler NO iniciado - falta configuración de Supabase")


# ============ RUN ============
if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
