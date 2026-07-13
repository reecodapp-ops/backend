"""Sync router (per blueprint F16). Requires X-Business-Id header.

    POST /sync     ingest an offline batch of events
"""
from __future__ import annotations

from fastapi import APIRouter, status

from app.core.master_data_deps import SyncServiceDep
from app.schemas.sync import SyncBatchRequest, SyncBatchResponse

router = APIRouter(prefix="/sync", tags=["Sync"])


@router.post(
    "",
    response_model=SyncBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest an offline batch of events (idempotent on client_id)",
)
async def post_sync_batch(
    payload: SyncBatchRequest, service: SyncServiceDep
) -> SyncBatchResponse:
    return await service.process_batch(payload)
