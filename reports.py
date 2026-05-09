import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import db

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)
FROM_NAME = os.getenv("FROM_NAME", "KAMApp")


def generate_weekly_report(manager_id):
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    monday_iso = monday.isoformat()
    prev_monday_iso = (monday - timedelta(days=7)).isoformat()
    month_start_iso = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Manager info
    mgr_list = db.query("profiles", select="full_name,role", filters={"id": manager_id})
    mgr = mgr_list[0] if mgr_list else {"full_name": "Manager", "role": "manager"}

    # Direct reports
    team = db.query("profiles", filters={"reports_to": manager_id, "is_active": True})
    kam_profiles = [p for p in team if p["role"] == "kam"]

    # Sub-teams for directors
    non_kam_ids = [p["id"] for p in team if p["role"] != "kam"]
    for mid in non_kam_ids:
        sub = db.query("profiles", filters={"reports_to": mid, "is_active": True, "role": "kam"})
        kam_profiles.extend(sub)

    kam_ids = [k["id"] for k in kam_profiles]
    if not kam_ids:
        return {"manager_name": mgr["full_name"], "period": f"{monday.strftime('%d/%m')} - {now.strftime('%d/%m/%Y')}",
                "kams": [], "totals": {}, "pipeline": {}, "alerts_count": 0}

    # All visits this week and last week for these KAMs
    all_visits = []
    all_prev_visits = []
    all_channels = []
    for kid in kam_ids:
        v = db.query("visits", select="kam_id,result,duration_minutes,checkin_at",
                      filters={"kam_id": kid, "checkin_at": {"gte": monday_iso}})
        all_visits.extend(v)
        pv = db.query("visits", select="kam_id",
                       filters={"kam_id": kid, "checkin_at": {"gte": prev_monday_iso, "lt": monday_iso}})
        all_prev_visits.extend(pv)
        ch = db.query("channels", select="id,assigned_to,pipeline_stage,status,created_at",
                       filters={"assigned_to": kid})
        all_channels.extend(ch)

    # Per-KAM stats
    kams_report = []
    for kam in kam_profiles:
        kv = [v for v in all_visits if v["kam_id"] == kam["id"]]
        kc = [c for c in all_channels if c["assigned_to"] == kam["id"]]
        pos = sum(1 for v in kv if v.get("result") == "positive")
        neg = sum(1 for v in kv if v.get("result") == "negative")
        durs = [v["duration_minutes"] for v in kv if v.get("duration_minutes")]
        kams_report.append({
            "name": kam["full_name"], "zone": kam.get("zone", ""),
            "visits_week": len(kv), "target": 12,
            "positive": pos, "negative": neg,
            "avg_duration": round(sum(durs)/len(durs)) if durs else 0,
            "pipeline_active": sum(1 for c in kc if c["pipeline_stage"] in ["first_contact","proposal","negotiation","onboarding"]),
        })
    kams_report.sort(key=lambda k: k["visits_week"]/max(k["target"],1))

    total_visits = len(all_visits)
    total_prev = len(all_prev_visits)
    total_target = len(kam_profiles) * 12
    new_channels = sum(1 for c in all_channels if c["created_at"] >= month_start_iso)

    pipeline = {}
    for s in ["lead","first_contact","proposal","negotiation","onboarding","active"]:
        pipeline[s] = sum(1 for c in all_channels if c["pipeline_stage"] == s)

    alerts_count = 0
    for kid in kam_ids:
        al = db.query("alerts", select="id", filters={"user_id": kid, "is_dismissed": False})
        alerts_count += len(al)

    return {
        "manager_name": mgr["full_name"],
        "period": f"{monday.strftime('%d/%m')} - {now.strftime('%d/%m/%Y')}",
        "kams": kams_report,
        "totals": {
            "visits_week": total_visits, "visits_prev_week": total_prev,
            "visits_change": total_visits - total_prev, "visits_target": total_target,
            "completion_pct": round((total_visits/max(total_target,1))*100),
            "new_channels_month": new_channels,
        },
        "pipeline": pipeline,
        "alerts_count": alerts_count,
    }


def send_report_email(to_email, to_name, report):
    if not SMTP_USER or not SMTP_PASS:
        logger.warning("SMTP no configurado, saltando envío")
        return

    subject = f"📊 KAMApp · Reporte semanal · {report['period']}"
    html = _build_html(to_name, report)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        logger.info(f"Reporte enviado a {to_email}")
    except Exception as e:
        logger.error(f"Error email a {to_email}: {e}")
        raise


def _build_html(name, r):
    t = r["totals"]
    ch = t.get("visits_change", 0)
    cc = "#16a34a" if ch > 0 else "#dc2626" if ch < 0 else "#6b7280"
    ci = "📈" if ch > 0 else "📉" if ch < 0 else "➡️"

    kr = ""
    for k in r["kams"]:
        ratio = k["visits_week"]/max(k["target"],1)
        c = "#16a34a" if ratio >= 1 else "#E87A1E" if ratio >= 0.6 else "#dc2626"
        kr += f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eef0f4;font-size:13px">{k["name"]}</td><td style="padding:8px;border-bottom:1px solid #eef0f4;font-size:13px;color:{c};font-weight:700;text-align:center">{k["visits_week"]}/{k["target"]}</td><td style="padding:8px;border-bottom:1px solid #eef0f4;font-size:13px;text-align:center;color:#5a6078">{k["positive"]}✓/{k["negative"]}✗</td><td style="padding:8px;border-bottom:1px solid #eef0f4;font-size:13px;text-align:center;color:#5a6078">{k["pipeline_active"]}</td></tr>'

    sl = {"lead":"Lead","first_contact":"Contacto","proposal":"Propuesta","negotiation":"Negociación","onboarding":"Alta","active":"Activo"}
    sc = {"lead":"#94a3b8","first_contact":"#3b82f6","proposal":"#8b5cf6","negotiation":"#E87A1E","onboarding":"#16a34a","active":"#059669"}
    mx = max(r["pipeline"].values()) if r["pipeline"] else 1
    pr = ""
    for s,cnt in r["pipeline"].items():
        w = int((cnt/max(mx,1))*200)
        pr += f'<tr><td style="padding:4px 8px;font-size:12px;color:#5a6078;text-align:right;width:90px">{sl.get(s,s)}</td><td style="padding:4px 8px"><div style="background:{sc.get(s,"#6366f1")};height:18px;width:{max(w,20)}px;border-radius:4px;display:inline-flex;align-items:center;justify-content:flex-end;padding-right:6px"><span style="font-size:10px;font-weight:700;color:#fff">{cnt}</span></div></td></tr>'

    al = f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:12px;padding:12px 16px;margin-bottom:16px"><span style="font-size:12px;color:#dc2626;font-weight:600">⚠️ {r["alerts_count"]} alertas activas</span></div>' if r.get("alerts_count",0) > 0 else ""

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"></head><body style="margin:0;padding:0;background:#f7f8fa;font-family:Arial,sans-serif">
<div style="max-width:600px;margin:0 auto;padding:24px 16px">
<div style="text-align:center;margin-bottom:24px"><span style="color:#E87A1E;font-size:18px;font-weight:800">KAMApp</span><div style="color:#5a6078;font-size:12px;margin-top:4px">Reporte semanal · {r["period"]}</div></div>
<div style="color:#1a1a2e;font-size:15px;margin-bottom:20px">Hola {name},</div>
<table style="width:100%;border-collapse:separate;border-spacing:8px 0;margin-bottom:20px"><tr>
<td style="background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:16px;text-align:center"><div style="font-size:28px;font-weight:800;color:#3b82f6">{t["visits_week"]}</div><div style="font-size:10px;color:#5a6078">Visitas / {t["visits_target"]}</div><div style="font-size:11px;color:{cc};margin-top:4px">{ci} {"+" if ch>0 else ""}{ch} vs ant.</div></td>
<td style="background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:16px;text-align:center"><div style="font-size:28px;font-weight:800;color:#8b5cf6">{t["new_channels_month"]}</div><div style="font-size:10px;color:#5a6078">Canales nuevos</div></td>
<td style="background:#fff;border:1px solid #dde1e8;border-radius:8px;padding:16px;text-align:center"><div style="font-size:28px;font-weight:800;color:#16a34a">{t["completion_pct"]}%</div><div style="font-size:10px;color:#5a6078">Cumplimiento</div></td>
</tr></table>
<div style="background:#fff;border:1px solid #dde1e8;border-radius:12px;padding:16px;margin-bottom:16px"><div style="font-size:13px;font-weight:700;margin-bottom:12px">Actividad por KAM</div><table style="width:100%;border-collapse:collapse"><tr style="border-bottom:2px solid #eef0f4"><th style="padding:6px 12px;text-align:left;font-size:10px;color:#5a6078">KAM</th><th style="padding:6px;text-align:center;font-size:10px;color:#5a6078">Visitas</th><th style="padding:6px;text-align:center;font-size:10px;color:#5a6078">Resultado</th><th style="padding:6px;text-align:center;font-size:10px;color:#5a6078">Pipeline</th></tr>{kr}</table></div>
<div style="background:#fff;border:1px solid #dde1e8;border-radius:12px;padding:16px;margin-bottom:16px"><div style="font-size:13px;font-weight:700;margin-bottom:12px">Pipeline</div><table>{pr}</table></div>
{al}
<div style="text-align:center;margin-top:24px;padding-top:16px;border-top:1px solid #eef0f4"><div style="font-size:11px;color:#8b90a0">KAMApp · Powered by Naturgy</div></div>
</div></body></html>'''
