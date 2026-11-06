"""E-invoice import and matcher run (spec §6, §7), owner actions, mutes and preferences (§7.5, §8). Behind
`require_feature()`; the worker's `ingest` scope posts the parsed CSV and triggers `/match`; every owner route
needs `write`, the rollout runbook dismiss `admin`. Static paths are declared before the `/{invoice_id}` routes."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..auth import require, require_feature
from ..database import get_db
from ..schemas.einvoices import (
    EinvoiceDismissIn, EinvoiceImportIn, EinvoiceImportOut, EinvoiceLinkIn, EinvoiceMatchOut, EinvoiceMuteOut,
    EinvoicePreferencesIn, EinvoicePreferencesOut, EinvoiceStateOut, RunbookDismissIn, RunbookDismissOut,
)
from ..services import einvoice_matcher, einvoice_service, settings_service
from .errors import service_errors

router = APIRouter(prefix="/einvoices", tags=["E-invoices"], dependencies=[Depends(require_feature())])

INGEST = [Depends(require("ingest"))]
WRITE = [Depends(require("write"))]
ADMIN = [Depends(require("admin"))]


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


def _preferences(settings: dict) -> dict:
    return {"enabled": settings["enabled"], "remind_unmatched": settings["remind_unmatched"],
            "version": settings["version"]}


@router.get("/mutes", response_model=list[EinvoiceMuteOut], dependencies=WRITE)
def list_mutes(db: Session = Depends(get_db)):
    return einvoice_service.list_mutes(db)


@router.delete("/mutes/{tax_id}", status_code=204, dependencies=WRITE)
def delete_mute(tax_id: str, db: Session = Depends(get_db)):
    with service_errors():
        einvoice_service.delete_mute(db, tax_id)
    db.commit()
    return Response(status_code=204)


@router.get("/preferences", response_model=EinvoicePreferencesOut, dependencies=WRITE)
def get_preferences(db: Session = Depends(get_db)):
    return _preferences(settings_service.read_einvoice_settings(db))


@router.patch("/preferences", response_model=EinvoicePreferencesOut, dependencies=WRITE)
def patch_preferences(body: EinvoicePreferencesIn, db: Session = Depends(get_db)):
    settings = settings_service.merge_einvoice_preferences(db, remind_unmatched=body.remind_unmatched)
    db.commit()
    return _preferences(settings)


@router.post("/runbook-dismiss", response_model=RunbookDismissOut, dependencies=ADMIN)  # rollout step (R1)
def runbook_dismiss(body: RunbookDismissIn, db: Session = Depends(get_db)):
    with service_errors():
        out = einvoice_service.runbook_dismiss(db, body.invoices)
    db.commit()
    return out


@router.post("/{invoice_id}/link", response_model=EinvoiceStateOut, dependencies=WRITE)
def link(invoice_id: int, body: EinvoiceLinkIn, db: Session = Depends(get_db)):
    with service_errors():
        out = einvoice_service.link(db, invoice_id, entry_id=body.entry_id, group_id=body.group_id)
    db.commit()
    return out


@router.post("/{invoice_id}/unlink", response_model=EinvoiceStateOut, dependencies=WRITE)
def unlink(invoice_id: int, db: Session = Depends(get_db)):
    with service_errors():
        out = einvoice_service.unlink(db, invoice_id)
    db.commit()
    return out


@router.post("/{invoice_id}/dismiss", response_model=EinvoiceStateOut, dependencies=WRITE)
def dismiss(invoice_id: int, body: EinvoiceDismissIn | None = None, db: Session = Depends(get_db)):
    with service_errors():
        out = einvoice_service.dismiss(db, invoice_id, mute_seller=body.mute_seller if body else False)
    db.commit()
    return out


@router.post("/{invoice_id}/undismiss", response_model=EinvoiceStateOut, dependencies=WRITE)
def undismiss(invoice_id: int, db: Session = Depends(get_db)):
    with service_errors():
        out = einvoice_service.undismiss(db, invoice_id)
    db.commit()
    return out
