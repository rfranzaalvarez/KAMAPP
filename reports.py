import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.getenv("FROM_NAME", "KAMApp")


def generate_weekly_report(supabase, manager_id):
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_str = monday.isoformat()
    prev_monday = (monday - timedelta(days=7)).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Get team
    team = supabase.table("profiles").select("*").eq(
        "reports_to", manager_id
    ).eq("is_active", True).execute().data or []

    manager_profile = supabase.table("profiles").select("role, full_name").eq(
        "id", manager_id
    ).single().execute().data or {}

    kam_profiles = [p for p in team if p["role"] == "kam"]

    # Include sub-teams for directors
    non_kam_ids = [p["id"] for p in team if p["role"] != "kam"]
    if non_kam_ids:
        sub_team = supabase.table("profiles").select("*").in_(
            "reports_to", non_kam_ids
        ).eq("is_active", True).eq("role", "kam").execute().data or []
        kam_profiles.extend(sub_team)

    kam_ids = [k["id"] for k in kam_profiles]

    if not kam_ids:
        return {
            "manager_name": manager_profile.get("full_name", ""),
            "period": f"{monday.strftime('%d/%m')} - {now.strftime('%d/%m/%Y')}",
            "kams": [], "totals": {}, "pipeline": {}, "alerts_count": 0,
        }

    # Visits this week
    visits_week = supabase.table("visits").select(
        "kam_id, result, duration_minutes"
    ).in_("kam_id", kam_ids).gte("checkin_at", monday_str).execute().data or []

    visits_prev = supabase.table("visits").select(
        "kam_id"
    ).in_("kam_id", kam_ids).gte("checkin_at", prev_monday).lt("checkin_at", monday_str).execute().data or []

    # Channels
    channels = supabase.table("channels").select(
        "id, assigned_to, pipeline_stage, status, created_at"
    ).in_("assigned_to", kam_ids).execute().data or []

    # Alerts
    alerts = supabase.table("alerts").select("id").in_(
        "user_id", kam_ids
    ).eq("is_dismissed", False).execute().data or []

    # Per-KAM stats
    kams_report = []
    for kam in kam_profiles:
        kam_visits = [v for v in visits_week if v["kam_id"] == kam["id"]]
        kam_channels = [c for c in channels if c["assigned_to"] == kam["id"]]
        positive = sum(1 for v in kam_visits if v.get("result") == "positive")
        negative = sum(1 for v in kam_visits if v.get("result") == "negative")
        durations = [v["duration_minutes"] for v in kam_visits if v.get("duration_minutes")]
        avg_duration = round(sum(durations) / len(durations)) if durations else 0

        kams_report.append({
            "name": kam["full_name"], "email": kam["email"], "zone": kam.get("zone", ""),
            "visits_week": len(kam_visits), "target": 12,
            "positive": positive, "negative": negative, "avg_duration": avg_duration,
            "total_channels": len(kam_channels),
            "active_channels": sum(1 for c in kam_channels if c["status"] == "active"),
            "pipeline_active": sum(1 for c in kam_channels if c["pipeline_stage"] in [
                "first_contact", "proposal", "negotiation", "onboarding"
            ]),
        })

    kams_report.sort(key=lambda k: k["visits_week"] / max(k["target"], 1))

    total_visits = len(visits_week)
    total_prev = len(visits_prev)
    total_target = len(kam_profiles) * 12
    new_channels = sum(1 for c in channels if c["created_at"] >= month_start)

    pipeline_summary = {}
    for stage in ["lead", "first_contact", "proposal", "negotiation", "onboarding", "active"]:
        pipeline_summary[stage] = sum(1 for c in channels if c["pipeline_stage"] == stage)

    return {
        "manager_name": manager_profile.get("full_name", "Manager"),
        "period": f"{monday.strftime('%d/%m')} - {now.strftime('%d/%m/%Y')}",
        "kams": kams_report,
        "totals": {
            "visits_week": total_visits, "visits_prev_week": total_prev,
            "visits_change": total_visits - total_prev, "visits_target": total_target,
            "completion_pct": round((total_visits / max(total_target, 1)) * 100),
            "new_channels_month": new_channels, "total_channels": len(channels),
        },
        "pipeline": pipeline_summary,
        "alerts_count": len(alerts),
    }


def send_report_email(to_email, to_name, report):
    if not SMTP_USER or not SMTP_PASS:
        logger.warning("SMTP no configurado, saltando envío")
        return

    subject = f"📊 KAMApp · Reporte semanal · {report['period']}"
    html = _build_report_html(to_name, report)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info(f"Reporte enviado a {to_email}")
    except Exception as e:
        logger.error(f"Error enviando email a {to_email}: {e}")
        raise


def _build_report_html(manager_name, report):
    totals = report["totals"]
    pipeline = report["pipeline"]
    kams = report["kams"]

    change = totals.get("visits_change", 0)
    change_icon = "📈" if change > 0 else "📉" if change < 0 else "➡️"
    change_color = "#16a34a" if change > 0 else "#dc2626" if change < 0 else "#6b7280"

    kam_rows = ""
    for kam in kams:
        ratio = kam["visits_week"] / max(kam["target"], 1)
        color = "#16a34a" if ratio >= 1 else "#E87A1E" if ratio >= 0.6 else "#dc2626"
        kam_rows += f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #eef0f4;font-size:13px;color:#1a1a2e;">{kam['name']}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eef0f4;font-size:13px;color:{color};font-weight:700;text-align:center;">{kam['visits_week']}/{kam['target']}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eef0f4;font-size:13px;text-align:center;color:#5a6078;">{kam['positive']}✓ / {kam['negative']}✗</td>
            <td style="padding:8px 12px;border-bottom:1px solid #eef0f4;font-size:13px;text-align:center;color:#5a6078;">{kam['pipeline_active']}</td>
        </tr>"""

    pipeline_labels = {
        "lead": "Lead", "first_contact": "Contacto", "proposal": "Propuesta",
        "negotiation": "Negociación", "onboarding": "Alta", "active": "Activo",
    }
    pipeline_colors = {
        "lead": "#94a3b8", "first_contact": "#3b82f6", "proposal": "#8b5cf6",
        "negotiation": "#E87A1E", "onboarding": "#16a34a", "active": "#059669",
    }
    max_p = max(pipeline.values()) if pipeline.values() else 1
    pipeline_rows = ""
    for stage, count in pipeline.items():
        w = int((count / max(max_p, 1)) * 200)
        c = pipeline_colors.get(stage, "#6366f1")
        pipeline_rows += f"""
        <tr>
            <td style="padding:4px 8px;font-size:12px;color:#5a6078;text-align:right;width:90px;">{pipeline_labels.get(stage, stage)}</td>
            <td style="padding:4px 8px;">
                <div style="background:{c};height:18px;width:{max(w, 20)}px;border-radius:4px;display:inline-flex;align-items:center;justify-content:flex-end;padding-right:6px;">
                    <span style="font-size:10px;font-weight:700;color:#fff;">{count}</span>
                </div>
            </td>
        </tr>"""

    alerts_section = ""
    if report.get("alerts_count", 0) > 0:
        alerts_section = f"""
        <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:12px 16px;margin-bottom:16px;">
            <span style="font-size:12px;color:#dc2626;font-weight:600;">⚠️ {report['alerts_count']} alertas activas en tu equipo</span>
        </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f7f8fa;font-family:'Segoe UI',Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;padding:24px 16px;">
    <div style="text-align:center;margin-bottom:24px;">
        <span style="color:#E87A1E;font-size:18px;font-weight:800;">KAMApp</span>
        <div style="color:#5a6078;font-size:12px;margin-top:4px;">Reporte semanal · {report['period']}</div>
    </div>
    <div style="color:#1a1a2e;font-size:15px;margin-bottom:20px;">Hola {manager_name},</div>
    <table style="width:100%;border-collapse:separate;border-spacing:8px 0;margin-bottom:20px;">
        <tr>
            <td style="background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:16px;text-align:center;">
                <div style="font-size:28px;font-weight:800;color:#3b82f6;">{totals['visits_week']}</div>
                <div style="font-size:10px;color:#5a6078;">Visitas / {totals['visits_target']} obj.</div>
                <div style="font-size:11px;color:{change_color};margin-top:4px;">{change_icon} {'+' if change > 0 else ''}{change} vs ant.</div>
            </td>
            <td style="background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:16px;text-align:center;">
                <div style="font-size:28px;font-weight:800;color:#8b5cf6;">{totals['new_channels_month']}</div>
                <div style="font-size:10px;color:#5a6078;">Canales nuevos (mes)</div>
            </td>
            <td style="background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:16px;text-align:center;">
                <div style="font-size:28px;font-weight:800;color:#16a34a;">{totals['completion_pct']}%</div>
                <div style="font-size:10px;color:#5a6078;">Cumplimiento</div>
            </td>
        </tr>
    </table>
    <div style="background:#fff;border:1px solid #dde1e8;border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:13px;font-weight:700;color:#1a1a2e;margin-bottom:12px;">Actividad por KAM</div>
        <table style="width:100%;border-collapse:collapse;">
            <tr style="border-bottom:2px solid #eef0f4;">
                <th style="padding:6px 12px;text-align:left;font-size:10px;color:#5a6078;">KAM</th>
                <th style="padding:6px 12px;text-align:center;font-size:10px;color:#5a6078;">Visitas</th>
                <th style="padding:6px 12px;text-align:center;font-size:10px;color:#5a6078;">Resultado</th>
                <th style="padding:6px 12px;text-align:center;font-size:10px;color:#5a6078;">Pipeline</th>
            </tr>
            {kam_rows}
        </table>
    </div>
    <div style="background:#fff;border:1px solid #dde1e8;border-radius:12px;padding:16px;margin-bottom:16px;">
        <div style="font-size:13px;font-weight:700;color:#1a1a2e;margin-bottom:12px;">Pipeline del equipo</div>
        <table style="border-collapse:collapse;">{pipeline_rows}</table>
    </div>
    {alerts_section}
    <div style="text-align:center;margin-top:24px;padding-top:16px;border-top:1px solid #eef0f4;">
        <div style="font-size:11px;color:#8b90a0;">Generado por KAMApp · Powered by Naturgy</div>
    </div>
</div>
</body></html>"""
