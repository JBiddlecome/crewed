"""
Recruiting module — admin side.
Prefix: /admin/recruiting
"""
import json
import os
import shutil
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..auth import require
from ..config import DATA_DIR
from ..db import get_db
from ..templating import flash, templates

router = APIRouter(prefix="/admin/recruiting")

ONBOARDING_PDF_DIR = DATA_DIR / "uploads" / "onboarding_pdfs"


# ── helpers ──────────────────────────────────────────────────────────────────

def _onboarding_progress(db: Session, employee: models.User) -> dict:
    """Return completion statistics for an employee's onboarding package."""
    package = (
        db.query(models.OnboardingPackageItem)
        .filter_by(required=True)
        .order_by(models.OnboardingPackageItem.sort_order)
        .all()
    )
    if not package:
        return {"total": 0, "complete": 0, "pct": 100}

    complete = 0
    for item in package:
        rec = (
            db.query(models.EmployeeOnboarding)
            .filter_by(employee_id=employee.id, document_id=item.document_id)
            .first()
        )
        if rec and rec.status == "complete":
            complete += 1

    total = len(package)
    pct = int(complete / total * 100) if total else 100
    return {"total": total, "complete": complete, "pct": pct}


def _ensure_onboarding_records(db: Session, employee: models.User):
    """Create EmployeeOnboarding rows for any package items the employee is missing."""
    package = (
        db.query(models.OnboardingPackageItem)
        .order_by(models.OnboardingPackageItem.sort_order)
        .all()
    )
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
    db.commit()


# ── Recruiting Dashboard ──────────────────────────────────────────────────────

@router.get("")
def recruiting_dashboard(
    request: Request,
    status: str = "",
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    q = db.query(models.User).filter_by(role="employee")
    if status in ("active", "pending", "disabled"):
        q = q.filter_by(status=status)
    employees = q.order_by(models.User.created_at.desc()).all()

    # Ensure onboarding records exist for all employees
    for emp in employees:
        _ensure_onboarding_records(db, emp)

    progress = {emp.id: _onboarding_progress(db, emp) for emp in employees}
    package_count = db.query(models.OnboardingPackageItem).filter_by(required=True).count()

    return templates.TemplateResponse(
        request,
        "admin/recruiting.html",
        {
            "user": user,
            "employees": employees,
            "progress": progress,
            "package_count": package_count,
            "filter_status": status,
        },
    )


# ── Document Library ──────────────────────────────────────────────────────────

@router.get("/documents")
def document_library(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    docs = (
        db.query(models.OnboardingDocument)
        .order_by(models.OnboardingDocument.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/recruiting_documents.html",
        {"user": user, "documents": docs},
    )


@router.post("/documents/upload")
async def upload_document(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    doc_type: str = Form("pdf"),
    requires_signature: bool = Form(False),
    pdf_file: UploadFile = File(None),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        flash(request, "Document name is required.", "error")
        return RedirectResponse("/admin/recruiting/documents", status_code=303)

    filename = None
    if doc_type == "pdf":
        if not pdf_file or not pdf_file.filename:
            flash(request, "A PDF file is required for document type 'PDF'.", "error")
            return RedirectResponse("/admin/recruiting/documents", status_code=303)
        ext = os.path.splitext(pdf_file.filename)[1].lower()
        if ext != ".pdf":
            flash(request, "Only PDF files are accepted.", "error")
            return RedirectResponse("/admin/recruiting/documents", status_code=303)
        filename = f"{uuid.uuid4()}.pdf"
        dest = ONBOARDING_PDF_DIR / filename
        ONBOARDING_PDF_DIR.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as buf:
            shutil.copyfileobj(pdf_file.file, buf)

    doc = models.OnboardingDocument(
        name=name,
        doc_type=doc_type,
        filename=filename,
        description=description.strip() or None,
        requires_signature=requires_signature,
    )
    db.add(doc)
    db.commit()
    flash(request, f"'{name}' added to document library.")
    return RedirectResponse("/admin/recruiting/documents", status_code=303)


@router.get("/documents/{doc_id}")
def document_editor(
    doc_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    doc = db.get(models.OnboardingDocument, doc_id)
    if not doc:
        flash(request, "Document not found.", "error")
        return RedirectResponse("/admin/recruiting/documents", status_code=303)

    return templates.TemplateResponse(
        request,
        "admin/recruiting_doc_editor.html",
        {"user": user, "doc": doc},
    )


@router.post("/documents/{doc_id}/fields/save")
async def save_fields(
    doc_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    """Accepts a JSON body: list of field objects with page, field_type, label, x_pct, y_pct, w_pct, h_pct, required."""
    doc = db.get(models.OnboardingDocument, doc_id)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    try:
        body = await request.json()
        fields_data = body.get("fields", [])
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Delete existing fields and recreate
    db.query(models.OnboardingField).filter_by(document_id=doc_id).delete()
    db.flush()

    for i, f in enumerate(fields_data):
        db.add(models.OnboardingField(
            document_id=doc_id,
            page=int(f.get("page", 1)),
            field_type=f.get("field_type", "text"),
            label=f.get("label", "Field"),
            x_pct=float(f.get("x_pct", 10)),
            y_pct=float(f.get("y_pct", 10)),
            w_pct=float(f.get("w_pct", 30)),
            h_pct=float(f.get("h_pct", 5)),
            required=bool(f.get("required", True)),
            sort_order=i,
        ))
    db.commit()
    return JSONResponse({"ok": True, "count": len(fields_data)})


@router.post("/documents/{doc_id}/delete")
def delete_document(
    doc_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    doc = db.get(models.OnboardingDocument, doc_id)
    if not doc:
        flash(request, "Document not found.", "error")
        return RedirectResponse("/admin/recruiting/documents", status_code=303)

    # Check if in package
    in_pkg = db.query(models.OnboardingPackageItem).filter_by(document_id=doc_id).first()
    if in_pkg:
        flash(request, "Remove this document from the onboarding package first.", "error")
        return RedirectResponse("/admin/recruiting/documents", status_code=303)

    # Delete file
    if doc.filename:
        try:
            (ONBOARDING_PDF_DIR / doc.filename).unlink(missing_ok=True)
        except Exception:
            pass

    name = doc.name
    db.delete(doc)
    db.commit()
    flash(request, f"'{name}' deleted.")
    return RedirectResponse("/admin/recruiting/documents", status_code=303)


# ── Onboarding Package ────────────────────────────────────────────────────────

@router.get("/package")
def package_manager(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    items = (
        db.query(models.OnboardingPackageItem)
        .order_by(models.OnboardingPackageItem.sort_order)
        .all()
    )
    in_package_ids = {item.document_id for item in items}
    available = (
        db.query(models.OnboardingDocument)
        .filter(~models.OnboardingDocument.id.in_(in_package_ids) if in_package_ids else True)
        .order_by(models.OnboardingDocument.name)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "admin/recruiting_package.html",
        {"user": user, "items": items, "available": available},
    )


@router.post("/package/add")
def package_add(
    request: Request,
    document_id: int = Form(...),
    required: bool = Form(True),
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    exists = db.query(models.OnboardingPackageItem).filter_by(document_id=document_id).first()
    if exists:
        flash(request, "That document is already in the package.", "warning")
        return RedirectResponse("/admin/recruiting/package", status_code=303)

    max_order = db.query(models.OnboardingPackageItem).count()
    db.add(models.OnboardingPackageItem(
        document_id=document_id,
        sort_order=max_order,
        required=required,
    ))
    db.commit()
    flash(request, "Document added to onboarding package.")
    return RedirectResponse("/admin/recruiting/package", status_code=303)


@router.post("/package/{item_id}/remove")
def package_remove(
    item_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    item = db.get(models.OnboardingPackageItem, item_id)
    if not item:
        flash(request, "Item not found.", "error")
        return RedirectResponse("/admin/recruiting/package", status_code=303)
    db.delete(item)
    db.commit()
    flash(request, "Removed from onboarding package.")
    return RedirectResponse("/admin/recruiting/package", status_code=303)


@router.post("/package/reorder")
async def package_reorder(
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    """Accepts JSON: {"order": [item_id, item_id, ...]}"""
    try:
        body = await request.json()
        order = body.get("order", [])
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    for idx, item_id in enumerate(order):
        item = db.get(models.OnboardingPackageItem, int(item_id))
        if item:
            item.sort_order = idx
    db.commit()
    return JSONResponse({"ok": True})


# ── Per-Employee Onboarding Detail ────────────────────────────────────────────

@router.get("/{employee_id}")
def employee_onboarding_detail(
    employee_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    emp = db.query(models.User).filter_by(id=employee_id, role="employee").first()
    if not emp:
        flash(request, "Employee not found.", "error")
        return RedirectResponse("/admin/recruiting", status_code=303)

    _ensure_onboarding_records(db, emp)

    package = (
        db.query(models.OnboardingPackageItem)
        .order_by(models.OnboardingPackageItem.sort_order)
        .all()
    )
    records = {
        r.document_id: r
        for r in db.query(models.EmployeeOnboarding).filter_by(employee_id=emp.id).all()
    }
    progress = _onboarding_progress(db, emp)

    return templates.TemplateResponse(
        request,
        "admin/recruiting_employee.html",
        {
            "user": user,
            "employee": emp,
            "package": package,
            "records": records,
            "progress": progress,
        },
    )


@router.get("/{employee_id}/wizard/{kind}.pdf")
def admin_wizard_pdf(
    employee_id: int,
    kind: str,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    from fastapi.responses import Response

    from ..pdf_forms import employer_info, fill_i9, fill_w4

    emp = db.query(models.User).filter_by(id=employee_id, role="employee").first()
    if not emp or kind not in ("w4", "i9"):
        flash(request, "Not found.", "error")
        return RedirectResponse("/admin/recruiting", status_code=303)
    doc = (
        db.query(models.OnboardingDocument)
        .filter_by(doc_type=f"{kind}_wizard")
        .first()
    )
    rec = (
        db.query(models.EmployeeOnboarding)
        .filter_by(employee_id=emp.id, document_id=doc.id)
        .first()
        if doc
        else None
    )
    if not rec or not rec.wizard_data:
        flash(request, f"{emp.name} hasn't completed the {kind.upper()} wizard yet.", "warning")
        return RedirectResponse(f"/admin/recruiting/{emp.id}", status_code=303)
    data = json.loads(rec.wizard_data)
    employer = employer_info(db)
    pdf = fill_w4(data, employer) if kind == "w4" else fill_i9(data, employer)
    filename = f"{'W-4' if kind == 'w4' else 'I-9'}_{emp.last_name}_{emp.first_name}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{employee_id}/activate")
def activate_employee(
    employee_id: int,
    request: Request,
    user: models.User = Depends(require("admin")),
    db: Session = Depends(get_db),
):
    emp = db.query(models.User).filter_by(id=employee_id, role="employee").first()
    if not emp:
        flash(request, "Employee not found.", "error")
        return RedirectResponse("/admin/recruiting", status_code=303)

    progress = _onboarding_progress(db, emp)
    if progress["pct"] < 100 and progress["total"] > 0:
        flash(
            request,
            f"{emp.name} has not completed all onboarding documents ({progress['complete']}/{progress['total']}).",
            "error",
        )
        return RedirectResponse(f"/admin/recruiting/{employee_id}", status_code=303)

    emp.status = "active"
    db.commit()
    flash(request, f"{emp.name} has been activated.")
    return RedirectResponse(f"/admin/recruiting/{employee_id}", status_code=303)
