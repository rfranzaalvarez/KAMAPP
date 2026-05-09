import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def run_alert_generation(supabase):
    """Genera alertas automáticas basadas en reglas de negocio."""
    results = {"created": 0, "skipped": 0}

    now = datetime.now()

    # 1. Canales sin visita en 10+ días
    threshold = (now - timedelta(days=10)).isoformat()

    channels = supabase.table("channels").select(
        "id, name, assigned_to"
    ).in_("status", ["active", "developing"]).execute().data or []

    for channel in channels:
        last_visit = supabase.table("visits").select("checkin_at").eq(
            "channel_id", channel["id"]
        ).order("checkin_at", desc=True).limit(1).execute().data

        if not last_visit or last_visit[0]["checkin_at"] < threshold:
            existing = supabase.table("alerts").select("id").eq(
                "channel_id", channel["id"]
            ).eq("alert_type", "channel_inactive").eq(
                "is_dismissed", False
            ).execute().data

            if not existing:
                days = 0
                if last_visit:
                    last_dt = datetime.fromisoformat(last_visit[0]["checkin_at"].replace("Z", "+00:00"))
                    days = (now - last_dt.replace(tzinfo=None)).days

                supabase.table("alerts").insert({
                    "user_id": channel["assigned_to"],
                    "channel_id": channel["id"],
                    "alert_type": "channel_inactive",
                    "title": f"{channel['name']} sin visita reciente",
                    "detail": f"Lleva {days} días sin visita registrada" if days > 0 else "Sin visitas registradas",
                    "priority": "high",
                }).execute()
                results["created"] += 1
            else:
                results["skipped"] += 1

    # 2. Canales estancados en pipeline (15+ días)
    stale_threshold = (now - timedelta(days=15)).isoformat()

    stale_channels = supabase.table("channels").select(
        "id, name, assigned_to, pipeline_stage"
    ).in_("pipeline_stage", [
        "first_contact", "proposal", "negotiation"
    ]).lt("updated_at", stale_threshold).execute().data or []

    stage_labels = {
        "first_contact": "Primer contacto",
        "proposal": "Propuesta",
        "negotiation": "Negociación",
    }

    for channel in stale_channels:
        existing = supabase.table("alerts").select("id").eq(
            "channel_id", channel["id"]
        ).eq("alert_type", "pipeline_stalled").eq(
            "is_dismissed", False
        ).execute().data

        if not existing:
            supabase.table("alerts").insert({
                "user_id": channel["assigned_to"],
                "channel_id": channel["id"],
                "alert_type": "pipeline_stalled",
                "title": f"{channel['name']} estancado en {stage_labels.get(channel['pipeline_stage'], channel['pipeline_stage'])}",
                "detail": "Lleva más de 15 días sin cambio de fase",
                "priority": "medium",
            }).execute()
            results["created"] += 1
        else:
            results["skipped"] += 1

    # 3. Planes de cuenta con revisión vencida
    today = now.strftime("%Y-%m-%d")
    try:
        overdue_plans = supabase.table("account_plans").select(
            "id, channel_id, kam_id"
        ).lt("review_date", today).eq("status", "active").execute().data or []

        for plan in overdue_plans:
            existing = supabase.table("alerts").select("id").eq(
                "channel_id", plan["channel_id"]
            ).eq("alert_type", "plan_review").eq(
                "is_dismissed", False
            ).execute().data

            if not existing:
                # Get channel name
                ch = supabase.table("channels").select("name").eq(
                    "id", plan["channel_id"]
                ).single().execute().data
                channel_name = ch["name"] if ch else "Canal"

                supabase.table("alerts").insert({
                    "user_id": plan["kam_id"],
                    "channel_id": plan["channel_id"],
                    "alert_type": "plan_review",
                    "title": f"Revisión de plan: {channel_name}",
                    "detail": "La fecha de revisión del plan de cuenta ha vencido",
                    "priority": "medium",
                }).execute()
                results["created"] += 1
    except Exception as e:
        logger.warning(f"Error revisando planes: {e}")

    logger.info(f"Alertas generadas: {results}")
    return results
