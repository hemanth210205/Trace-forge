from fastapi import APIRouter, status

from app.api.deps import CurrentApiKey, DbSession
from app.schemas.trace import IngestBatch, IngestResult
from app.services.ingest import ingest_batch

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post(
    "/batch",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestResult,
    summary="Ingest a batch of traces and spans (project API key)",
    responses={401: {"description": "Missing, invalid or revoked API key"}, 413: {"description": "Body too large"}},
)
def ingest(batch: IngestBatch, ctx: CurrentApiKey, db: DbSession) -> IngestResult:
    """Upserts traces and spans into the API key's project. Idempotent: resending a batch is safe.
    The whole batch is validated and stored atomically; any invalid item rejects the batch with 422."""
    traces, spans = ingest_batch(db, ctx.project.id, batch)
    db.commit()
    return IngestResult(accepted_traces=traces, accepted_spans=spans)
