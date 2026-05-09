import logging
from datetime import datetime, timedelta
import db

logger = logging.getLogger(__name__)


def run_alert_generation():
    results = {"created": 0, "skipped": 0}
    now = datetime.now()

    # 1. Channels without visit in 10+ days
    threshold = (now - timedelta(days=10)).isoformat()
    channels = db.query("channels", select="id,name,assigned_to",
                        filters={"status": {"in": "(active,developing)"}})

    for ch in channels:
        visits = db.query("visits", select="checkin_at",
                          filters={"channel_id": ch["id"]},
                          order="checkin_at.desc", limit=1)

        if not visits or visits[0]["checkin_at"] < threshold:
            existing = db.query("alerts", select="id",
                                filters={"channel_id": ch["id"], "alert_type": "channel_inactive", "is_dismissed": False})
            if not existing:
                days = 0
                if visits:
                    try:
                        last_dt = datetime.fromisoformat(visits[0]["checkin_at"].replace("Z", "+00:00"))
                        days = (now - last_dt.replace(tzinfo=None)).days
                    except:
                        pass

                db.insert("alerts", {
                    "user_id": ch["assigned_to"],
                    "channel_id": ch["id"],
                    "alert_type": "channel_inactive",
                    "title": f"{ch['name']} sin visita reciente",
                    "detail": f"Lleva {days} días sin visita" if days > 0 else "Sin visitas registradas",
                    "priority": "high",
                })
                results["created"] += 1
            else:
                results["skipped"] += 1

    # 2. Stalled pipeline (15+ days)
    stale = (now - timedelta(days=15)).isoformat()
    stale_channels = db.query("channels", select="id,name,assigned_to,pipeline_stage",
                              filters={"pipeline_stage": {"in": "(first_contact,proposal,negotiation)"},
                                       "updated_at": {"lt": stale}})

    labels = {"first_contact": "Primer contacto", "proposal": "Propuesta", "negotiation": "Negociación"}
    for ch in stale_channels:
        existing = db.query("alerts", select="id",
                            filters={"channel_id": ch["id"], "alert_type": "pipeline_stalled", "is_dismissed": False})
        if not existing:
            db.insert("alerts", {
                "user_id": ch["assigned_to"],
                "channel_id": ch["id"],
                "alert_type": "pipeline_stalled",
                "title": f"{ch['name']} estancado en {labels.get(ch['pipeline_stage'], ch['pipeline_stage'])}",
                "detail": "Más de 15 días sin cambio de fase",
                "priority": "medium",
            })
            results["created"] += 1
        else:
            results["skipped"] += 1

    # 3. Overdue plan reviews
    today = now.strftime("%Y-%m-%d")
    try:
        plans = db.query("account_plans", select="id,channel_id,kam_id",
                         filters={"review_date": {"lt": today}, "status": "active"})
        for plan in plans:
            existing = db.query("alerts", select="id",
                                filters={"channel_id": plan["channel_id"], "alert_type": "plan_review", "is_dismissed": False})
            if not existing:
                chs = db.query("channels", select="name", filters={"id": plan["channel_id"]})
                cname = chs[0]["name"] if chs else "Canal"
                db.insert("alerts", {
                    "user_id": plan["kam_id"],
                    "channel_id": plan["channel_id"],
                    "alert_type": "plan_review",
                    "title": f"Revisión de plan: {cname}",
                    "detail": "Fecha de revisión vencida",
                    "priority": "medium",
                })
                results["created"] += 1
    except Exception as e:
        logger.warning(f"Error planes: {e}")

    logger.info(f"Alertas: {results}")
    return results
