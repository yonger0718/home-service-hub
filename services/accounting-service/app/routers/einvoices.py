"""E-invoice import and matcher run (spec §6, §7). Behind `require_feature()`; the worker's `ingest` scope posts
the parsed CSV and triggers `/match`."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db
from ..schemas.einvoices import EinvoiceImportIn, EinvoiceImportOut, EinvoiceMatchOut
from ..services import einvoice_matcher, einvoice_service
from .errors import service_errors

router = APIRouter(prefix="/einvoices", tags=["E-invoices"], dependencies=[Depends(require_feature())])

INGEST = [Depends(require("ingest"))]


@router.post("/import", response_model=EinvoiceImportOut, dependencies=INGEST)
def ingest(body: EinvoiceImportIn, db: Session = Depends(get_db)):
    with service_errors():
        einvoice_service.lock(db)
        counts = einvoice_service.import_invoices(db, body)
        match_out = einvoice_matcher.run_matcher(db)  # same transaction as the import
    db.commit()
    return {**counts, "match": match_out}


@router.post("/match", response_model=EinvoiceMatchOut, dependencies=INGEST)
def match(db: Session = Depends(get_db)):
    with service_errors():
        out = einvoice_matcher.run_matcher(db)
    db.commit()
    return out
