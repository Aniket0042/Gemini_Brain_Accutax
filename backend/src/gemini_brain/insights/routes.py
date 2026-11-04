import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from gemini_brain.insights.auth import AuthContext, get_insight_auth
from gemini_brain.insights.bedrock.client import BedrockGenerationError, get_bedrock_client
from gemini_brain.insights.cache import get_cached, make_cache_key, set_cached
from gemini_brain.insights.config import get_settings
from gemini_brain.insights.rate_limit import check_and_record
from gemini_brain.insights.schemas.request import InsightRequest
from gemini_brain.insights.schemas.response import Bullet, InsightResponse
from gemini_brain.insights.widgets.registry import REGISTRY, get_widget_spec

logger = logging.getLogger("gemini_brain.insights")
router = APIRouter(prefix="/api/v1/insight", tags=["Insights"])


@router.get("/widgets")
def list_widgets():
    """Lists supported widgetIds and the JSON schema each one expects in `data`."""
    return {
        widget_id: {
            "displayName": spec.display_name,
            "dataSchema": spec.data_model.model_json_schema(),
        }
        for widget_id, spec in REGISTRY.items()
    }


@router.post("/generate", response_model=InsightResponse)
async def generate_insight(
    request: InsightRequest,
    auth: AuthContext = Depends(get_insight_auth),
):
    # organizationId in the body is only a client-side hint; auth.organization_ids
    # (the caller's real org memberships, confirmed live against the Accutax
    # backend -- see app/auth.py) is the source of truth. Reject outright if
    # the requested org isn't one the caller actually belongs to, since that
    # only happens if the client is misconfigured or spoofing another tenant.
    if request.organizationId not in auth.organization_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organizationId is not one of the authenticated user's organizations",
        )

    try:
        spec = get_widget_spec(request.widgetId)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown widgetId '{request.widgetId}'")

    try:
        validated_data = spec.data_model.model_validate(request.data)
    except ValidationError as exc:
        # exc.errors() includes a non-JSON-serializable exception object in
        # "ctx" for custom validators -- strip down to plain fields only.
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=errors)

    date_range_dict = request.dateRange.model_dump() if request.dateRange else None
    cache_key = make_cache_key(request.organizationId, request.widgetId, date_range_dict, request.data)

    cached = get_cached(cache_key)
    if cached is not None:
        return cached.model_copy(update={"cached": True})

    if not check_and_record(request.organizationId):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Insight generation rate limit exceeded for this organization. Try again later.",
        )

    analysis = spec.analyze(validated_data)

    try:
        narrative = await get_bedrock_client().generate_narrative(spec.display_name, analysis.facts_text)
    except BedrockGenerationError as exc:
        logger.error("Insight generation failed for widget=%s org=%s: %s", request.widgetId, request.organizationId, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Insight generation is temporarily unavailable. Please try again shortly.",
        )

    narrative_bullets = [
        Bullet(type=item.get("type", "observation"), text=item.get("text", ""))
        for item in narrative.get("insights", [])
    ]

    response = InsightResponse(
        widgetId=request.widgetId,
        summary=narrative.get("summary", ""),
        bullets=[*analysis.metric_bullets, *narrative_bullets],
        chart=analysis.chart,
        model=get_settings().bedrock_model_id,
        generatedAt=datetime.now(timezone.utc),
        cached=False,
    )

    set_cached(cache_key, response)
    return response
