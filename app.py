import os
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


# ============ CALENDAR INVITE ============
@app.route("/api/calendar-invite", methods=["POST"])
def send_calendar_invite():
    """Envía un email de invitación de visita al KAM."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    kam_email = data.get("kam_email")
    kam_name = data.get("kam_name", "KAM")
    channel_name = data.get("channel_name", "Canal")
    channel_address = data.get("channel_address", "")
    planned_date = data.get("planned_date", "")
    planned_time = data.get("planned_time", "")
    notes = data.get("notes", "")

    if not kam_email:
        return jsonify({"error": "kam_email required"}), 400

    # Formatear fecha en español
    try:
        from datetime import datetime
        dt = datetime.strptime(planned_date, "%Y-%m-%d")
        dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                 "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        fecha_str = f"{dias[dt.weekday()]} {dt.day} de {meses[dt.month - 1]} de {dt.year}"
    except Exception:
        fecha_str = planned_date

    hora_str = planned_time[:5] if planned_time else ""

    subject = f"Visita planificada: {channel_name} - {fecha_str}"

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto;">
      <div style="background: #003E6B; padding: 20px; border-radius: 12px 12px 0 0;">
        <h2 style="color: white; margin: 0; font-size: 18px;">📅 Visita planificada</h2>
        <p style="color: #8b90a0; margin: 5px 0 0; font-size: 12px;">CRM para KAMs · Naturgy</p>
      </div>
      <div style="background: #f7f8fa; padding: 24px; border: 1px solid #dde1e8; border-top: none; border-radius: 0 0 12px 12px;">
        <p style="color: #1a1a2e; font-size: 14px; margin: 0 0 16px;">Hola <strong>{kam_name}</strong>,</p>
        <p style="color: #5a6078; font-size: 13px; margin: 0 0 20px;">
          Tienes una visita planificada:
        </p>
        <div style="background: white; border: 1px solid #dde1e8; border-left: 4px solid #E87A1E; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
          <div style="font-size: 16px; font-weight: bold; color: #1a1a2e; margin-bottom: 8px;">
            {channel_name}
          </div>
          <div style="font-size: 13px; color: #5a6078; margin-bottom: 4px;">
            📅 <strong>{fecha_str}</strong>
          </div>
          <div style="font-size: 13px; color: #5a6078; margin-bottom: 4px;">
            🕐 <strong>{hora_str}h</strong>
          </div>
          {"<div style='font-size: 13px; color: #5a6078; margin-bottom: 4px;'>📍 " + channel_address + "</div>" if channel_address else ""}
          {"<div style='font-size: 12px; color: #8b90a0; margin-top: 8px; padding-top: 8px; border-top: 1px solid #eef0f4;'>📝 " + notes + "</div>" if notes else ""}
        </div>
        <p style="color: #8b90a0; font-size: 11px; margin: 16px 0 0;">
          Este email se ha enviado automáticamente desde el CRM para KAMs.
        </p>
      </div>
    </div>
    """

    try:
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_user)
        from_name = os.environ.get("SMTP_FROM_NAME", "CRM para KAMs")

        if not smtp_user or not smtp_pass:
            return jsonify({"error": "SMTP not configured"}), 500

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{from_name} <{from_email}>"
        msg["To"] = kam_email
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, kam_email, msg.as_string())

        logger.info(f"Calendar invite sent to {kam_email} for {channel_name} on {planned_date}")
        return jsonify({"status": "sent", "to": kam_email})

    except Exception as e:
        logger.error(f"Error sending calendar invite: {e}")
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


# ============ TEST ENDPOINTS ============
@app.route("/api/test/smtp")
def test_smtp():
    """GET para probar que el SMTP funciona. Envía un email de prueba."""
    to_email = request.args.get("to")
    if not to_email:
        return jsonify({"error": "Añade ?to=tu@email.com"}), 400

    try:
        smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        from_email = os.environ.get("SMTP_FROM_EMAIL", smtp_user)

        if not smtp_user or not smtp_pass:
            return jsonify({"error": "SMTP_USER o SMTP_PASS no configurados", "smtp_user": smtp_user[:3] + "***" if smtp_user else "VACÍO"}), 500

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Test SMTP - CRM para KAMs"
        msg["From"] = f"CRM para KAMs <{from_email}>"
        msg["To"] = to_email
        msg.attach(MIMEText("<h2>SMTP funciona correctamente</h2><p>Este es un email de prueba del CRM para KAMs.</p>", "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        return jsonify({"status": "sent", "to": to_email, "from": from_email, "smtp_host": smtp_host})

    except Exception as e:
        return jsonify({"error": str(e), "smtp_host": os.environ.get("SMTP_HOST", ""), "smtp_user": os.environ.get("SMTP_USER", "")[:5] + "***"}), 500


@app.route("/api/test/report")
def test_weekly_report():
    """GET para disparar el reporte semanal manualmente."""
    if not db.is_configured():
        return jsonify({"error": "Supabase no configurado"}), 500
    key = request.args.get("api_key")
    if key != API_SECRET:
        return jsonify({"error": "Unauthorized - añade ?api_key=TU_API_SECRET"}), 401
    try:
        results = run_weekly_reports()
        return jsonify({"status": "ok", "results": results})
    except Exception as e:
        logger.error(f"Error en reporte test: {e}")
        return jsonify({"error": str(e)}), 500


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
