"""
Employee-side onboarding routes.
These are registered onto the same `router` object imported from employee.py
by calling register_onboarding_routes(router) at module load time.
"""
import json as _json
from datetime import datetime

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..db import get_db
from ..helpers import get_past_due_assignment, unread_count
from ..templating import flash, templates


def _ensure_onboarding_records(db: Session, employee: models.User):
    """Create EmployeeOnboarding rows for any package items the employee is missing."""
    package = (
        db.query(models.OnboardingPackageItem)
        .order_by(models.OnboardingPackageItem.sort_order)
        .all()
    )
    changed = False
    for item in package:
        exists = (
            db.query(models.EmployeeOnboarding)
            .filter_by(employee_id=employee.id, document_id=item.document_id)
            .first()
        )
        if not exists:
            db.add(
                models.EmployeeOnboarding(
                    employee_id=employee.id,
                    document_id=item.document_id,
                    status="not_started",
                )
            )
            changed = True
    if changed:
        db.commit()


def register_onboarding_routes(router):

    @router.get("/onboarding")
    def onboarding_overview(
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        _ensure_onboarding_records(db, user)
        package = (
            db.query(models.OnboardingPackageItem)
            .order_by(models.OnboardingPackageItem.sort_order)
            .all()
        )
        records = {
            r.document_id: r
            for r in db.query(models.EmployeeOnboarding).filter_by(employee_id=user.id).all()
        }
        total = sum(1 for i in package if i.required)
        complete = sum(
            1
            for i in package
            if i.required
            and records.get(i.document_id)
            and records[i.document_id].status == "complete"
        )
        all_done = total == 0 or complete == total
        return templates.TemplateResponse(
            request,
            "employee/onboarding.html",
            {
                "user": user,
                "package": package,
                "records": records,
                "total": total,
                "complete": complete,
                "all_done": all_done,
                "unread": unread_count(db, user),
                "blocked_timesheet": get_past_due_assignment(db, user.id),
            },
        )

    # ── W-4 Wizard (registered BEFORE /{doc_id} to avoid route clash) ──────

    @router.get("/onboarding/wizard/w4")
    def w4_wizard(
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        doc = db.query(models.OnboardingDocument).filter_by(doc_type="w4_wizard").first()
        if not doc:
            flash(request, "W-4 wizard not configured.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        _ensure_onboarding_records(db, user)
        rec = db.query(models.EmployeeOnboarding).filter_by(
            employee_id=user.id, document_id=doc.id
        ).first()
        existing = {}
        if rec and rec.wizard_data:
            try:
                existing = _json.loads(rec.wizard_data)
            except Exception:
                pass
        return templates.TemplateResponse(
            request,
            "employee/onboarding_w4.html",
            {
                "user": user,
                "doc": doc,
                "rec": rec,
                "data": existing,
                "unread": unread_count(db, user),
                "blocked_timesheet": get_past_due_assignment(db, user.id),
            },
        )

    @router.post("/onboarding/wizard/w4")
    async def w4_submit(
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        doc = db.query(models.OnboardingDocument).filter_by(doc_type="w4_wizard").first()
        if not doc:
            flash(request, "W-4 wizard not found.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        _ensure_onboarding_records(db, user)
        rec = db.query(models.EmployeeOnboarding).filter_by(
            employee_id=user.id, document_id=doc.id
        ).first()
        form_data = await request.form()
        wizard_data = {
            "first_name": form_data.get("first_name", "").strip(),
            "last_name": form_data.get("last_name", "").strip(),
            "ssn_last4": form_data.get("ssn_last4", "").strip(),
            "address": form_data.get("address", "").strip(),
            "city_state_zip": form_data.get("city_state_zip", "").strip(),
            "filing_status": form_data.get("filing_status", "").strip(),
            "multiple_jobs": form_data.get("multiple_jobs", "").strip(),
            "qualifying_children": form_data.get("qualifying_children", "0").strip(),
            "other_dependents": form_data.get("other_dependents", "0").strip(),
            "other_income": form_data.get("other_income", "0").strip(),
            "deductions": form_data.get("deductions", "0").strip(),
            "extra_withholding": form_data.get("extra_withholding", "0").strip(),
            "signature": form_data.get("signature", "").strip(),
            "sign_date": form_data.get("sign_date", "").strip(),
        }
        errors = []
        if not wizard_data["first_name"]:
            errors.append("First name is required.")
        if not wizard_data["last_name"]:
            errors.append("Last name is required.")
        if not wizard_data["filing_status"]:
            errors.append("Filing status is required.")
        if not wizard_data["signature"]:
            errors.append("Signature is required.")
        if not wizard_data["sign_date"]:
            errors.append("Date is required.")
        if errors:
            flash(request, " ".join(errors), "error")
            return RedirectResponse("/employee/onboarding/wizard/w4", status_code=303)
        rec.wizard_data = _json.dumps(wizard_data)
        rec.status = "complete"
        rec.completed_at = datetime.utcnow()
        db.commit()
        flash(request, "W-4 completed and saved!")
        return RedirectResponse("/employee/onboarding", status_code=303)

    # ── I-9 Wizard ──────────────────────────────────────────────────────────

    @router.get("/onboarding/wizard/i9")
    def i9_wizard(
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        doc = db.query(models.OnboardingDocument).filter_by(doc_type="i9_wizard").first()
        if not doc:
            flash(request, "I-9 wizard not configured.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        _ensure_onboarding_records(db, user)
        rec = db.query(models.EmployeeOnboarding).filter_by(
            employee_id=user.id, document_id=doc.id
        ).first()
        existing = {}
        if rec and rec.wizard_data:
            try:
                existing = _json.loads(rec.wizard_data)
            except Exception:
                pass
        return templates.TemplateResponse(
            request,
            "employee/onboarding_i9.html",
            {
                "user": user,
                "doc": doc,
                "rec": rec,
                "data": existing,
                "unread": unread_count(db, user),
                "blocked_timesheet": get_past_due_assignment(db, user.id),
            },
        )

    @router.post("/onboarding/wizard/i9")
    async def i9_submit(
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        doc = db.query(models.OnboardingDocument).filter_by(doc_type="i9_wizard").first()
        if not doc:
            flash(request, "I-9 wizard not found.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        _ensure_onboarding_records(db, user)
        rec = db.query(models.EmployeeOnboarding).filter_by(
            employee_id=user.id, document_id=doc.id
        ).first()
        form_data = await request.form()
        wizard_data = {
            "first_name": form_data.get("first_name", "").strip(),
            "middle_initial": form_data.get("middle_initial", "").strip(),
            "last_name": form_data.get("last_name", "").strip(),
            "other_names": form_data.get("other_names", "").strip(),
            "address": form_data.get("address", "").strip(),
            "apt": form_data.get("apt", "").strip(),
            "city": form_data.get("city", "").strip(),
            "state": form_data.get("state", "").strip(),
            "zip": form_data.get("zip", "").strip(),
            "dob": form_data.get("dob", "").strip(),
            "ssn": form_data.get("ssn", "").strip(),
            "email": form_data.get("email", "").strip(),
            "phone": form_data.get("phone", "").strip(),
            "citizenship_status": form_data.get("citizenship_status", "").strip(),
            "alien_reg_num": form_data.get("alien_reg_num", "").strip(),
            "i94_num": form_data.get("i94_num", "").strip(),
            "foreign_passport": form_data.get("foreign_passport", "").strip(),
            "work_auth_expiry": form_data.get("work_auth_expiry", "").strip(),
            "signature": form_data.get("signature", "").strip(),
            "sign_date": form_data.get("sign_date", "").strip(),
            "doc_list_a": form_data.get("doc_list_a", "").strip(),
            "doc_list_b": form_data.get("doc_list_b", "").strip(),
            "doc_list_c": form_data.get("doc_list_c", "").strip(),
        }
        errors = []
        if not wizard_data["first_name"]:
            errors.append("First name is required.")
        if not wizard_data["last_name"]:
            errors.append("Last name is required.")
        if not wizard_data["citizenship_status"]:
            errors.append("Citizenship/immigration status is required.")
        if not wizard_data["signature"]:
            errors.append("Signature is required.")
        if not wizard_data["sign_date"]:
            errors.append("Date is required.")
        if errors:
            flash(request, " ".join(errors), "error")
            return RedirectResponse("/employee/onboarding/wizard/i9", status_code=303)
        rec.wizard_data = _json.dumps(wizard_data)
        rec.status = "complete"
        rec.completed_at = datetime.utcnow()
        db.commit()
        flash(request, "I-9 completed and saved!")
        return RedirectResponse("/employee/onboarding", status_code=303)

    # ── PDF Document Completion ──────────────────────────────────────────────

    @router.get("/onboarding/{doc_id}")
    def onboarding_document(
        doc_id: int,
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        pkg_item = db.query(models.OnboardingPackageItem).filter_by(document_id=doc_id).first()
        if not pkg_item:
            flash(request, "Not in your onboarding package.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        doc = db.get(models.OnboardingDocument, doc_id)
        if not doc:
            flash(request, "Document not found.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        if doc.doc_type == "w4_wizard":
            return RedirectResponse("/employee/onboarding/wizard/w4", status_code=303)
        if doc.doc_type == "i9_wizard":
            return RedirectResponse("/employee/onboarding/wizard/i9", status_code=303)
        _ensure_onboarding_records(db, user)
        rec = db.query(models.EmployeeOnboarding).filter_by(
            employee_id=user.id, document_id=doc_id
        ).first()
        existing_values = {}
        if rec:
            for fv in rec.field_values:
                existing_values[fv.field_id] = fv.value
        return templates.TemplateResponse(
            request,
            "employee/onboarding_doc.html",
            {
                "user": user,
                "doc": doc,
                "rec": rec,
                "existing_values": existing_values,
                "unread": unread_count(db, user),
                "blocked_timesheet": get_past_due_assignment(db, user.id),
            },
        )

    @router.post("/onboarding/{doc_id}/save")
    async def onboarding_save_doc(
        doc_id: int,
        request: Request,
        user: models.User = Depends(require("employee")),
        db: Session = Depends(get_db),
    ):
        pkg_item = db.query(models.OnboardingPackageItem).filter_by(document_id=doc_id).first()
        if not pkg_item:
            flash(request, "Not in your onboarding package.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        doc = db.get(models.OnboardingDocument, doc_id)
        if not doc:
            flash(request, "Document not found.", "error")
            return RedirectResponse("/employee/onboarding", status_code=303)
        _ensure_onboarding_records(db, user)
        rec = db.query(models.EmployeeOnboarding).filter_by(
            employee_id=user.id, document_id=doc_id
        ).first()
        form_data = await request.form()
        missing_required = []
        for field in doc.fields:
            key = f"field_{field.id}"
            value = form_data.get(key, "").strip()
            if field.required and not value:
                missing_required.append(field.label)
                continue
            fv = db.query(models.EmployeeOnboardingFieldValue).filter_by(
                onboarding_id=rec.id, field_id=field.id
            ).first()
            if fv:
                fv.value = value
            else:
                db.add(
                    models.EmployeeOnboardingFieldValue(
                        onboarding_id=rec.id, field_id=field.id, value=value
                    )
                )
        if missing_required:
            db.commit()
            flash(request, f"Please fill in: {', '.join(missing_required)}", "error")
            return RedirectResponse(f"/employee/onboarding/{doc_id}", status_code=303)
        rec.status = "complete"
        rec.completed_at = datetime.utcnow()
        db.commit()
        flash(request, f"'{doc.name}' completed!")
        return RedirectResponse("/employee/onboarding", status_code=303)
