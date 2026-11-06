"""E-invoice import (spec §6). Behind `require_feature()`; the worker's `ingest` scope posts the parsed CSV."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db
from ..schemas.einvoices import EinvoiceImportIn, EinvoiceImportOut, EinvoiceMatchOut
from ..services import einvoice_service
from .errors import service_errors

router = APIRouter(prefix="/einvoices", tags=["E-invoices"], dependencies=[Depends(require_feature())])

INGEST = [Depends(require("ingest"))]


@router.post("/import", response_model=EinvoiceImportOut, dependencies=INGEST)
def ingest(body: EinvoiceImportIn, db: Session = Depends(get_db)):
    with service_errors():
        einvoice_service.lock(db)
        counts = einvoice_service.import_invoices(db, body)
        match = EinvoiceMatchOut(ran=False, processed=0, linked=0, ambiguous=0, near=0, unmatched=0, unchanged=0)
    db.commit()
    return {**counts, "match": match}
