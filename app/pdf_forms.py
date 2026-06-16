"""Generate filled, official W-4, I-9, and DE-4 PDFs from onboarding wizard data.

Templates are the unmodified government forms in ./forms:
  fw4.pdf  — irs.gov/pub/irs-pdf/fw4.pdf (always current revision)
  i9.pdf   — uscis.gov/i-9, Edition 01/20/25, expires 05/31/2027
  de4.pdf  — edd.ca.gov/pdf_pub_ctr/de4.pdf (California EDD)

Field names were verified against those editions; re-verify whenever a new
edition is downloaded.  DE-4 field names must be re-mapped if the EDD
releases a new revision — run PyPDFForm.PdfWrapper(path).schema to inspect.
"""

from PyPDFForm import PdfWrapper, RawElements
from sqlalchemy.orm import Session

RawText = RawElements.RawText

from .config import BASE_DIR
from .helpers import get_setting

FORMS_DIR = BASE_DIR / "forms"

W4_TEMPLATE = FORMS_DIR / "fw4.pdf"
I9_TEMPLATE = FORMS_DIR / "i9.pdf"
DE4_TEMPLATE = FORMS_DIR / "de4.pdf"


def employer_info(db: Session) -> dict:
    """Agency-as-employer details (set under Admin → Settings)."""
    return {
        "name": get_setting(db, "employer_name", "") or "",
        "address": get_setting(db, "employer_address", "") or "",
        "ein": get_setting(db, "employer_ein", "") or "",
    }


def _money(value) -> str:
    """'3' (children count already multiplied) / '250.50' → clean string."""
    try:
        f = float(str(value).replace(",", "").replace("$", "") or 0)
    except ValueError:
        return ""
    if f == 0:
        return ""
    return f"{f:,.2f}".rstrip("0").rstrip(".")


def _int(value) -> int:
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def _us_date(value: str) -> str:
    """ISO (yyyy-mm-dd, from <input type=date>) → mm/dd/yyyy; else pass through."""
    value = (value or "").strip()
    parts = value.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[1]}/{parts[2]}/{parts[0]}"
    return value


def _digits(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _city_state_zip(data: dict) -> str:
    """Build 'City, ST ZIP' from split fields; fall back to old combined field."""
    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    zip_ = (data.get("zip") or "").strip()
    if city or state or zip_:
        parts = []
        if city and state:
            parts.append(f"{city}, {state}")
        elif city:
            parts.append(city)
        elif state:
            parts.append(state)
        if zip_:
            parts.append(zip_)
        return " ".join(parts)
    return (data.get("city_state_zip") or "").strip()


# ── W-4 (2026 revision field map, verified visually) ──────────────────────────
# f1_01 first name+MI · f1_02 last · f1_03 address · f1_04 city/state/zip
# f1_05 SSN · c1_1[0..2] filing status · c1_2 step 2(c) box · c1_3 exempt box
# f1_06 3(a) children $ · f1_07 3(b) other deps $ · f1_08 line 3 total
# f1_09 4(a) · f1_10 4(b) · f1_11 4(c)
# f1_12 employer name/address · f1_13 first date of employment · f1_14 EIN
# Signature/date are not form fields — drawn as text.

W4_FILING_STATUS = {
    "Single": "c1_1[0]",
    "Married": "c1_1[1]",
    "Head of Household": "c1_1[2]",
}


def fill_w4(data: dict, employer: dict) -> bytes:
    ssn = _digits(data.get("ssn", ""))
    if len(ssn) == 9:
        ssn_display = f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}"
    elif data.get("ssn_last4"):
        ssn_display = f"XXX-XX-{data['ssn_last4']}"
    else:
        ssn_display = ""

    children_amt = _int(data.get("qualifying_children")) * 2000
    other_dep_amt = _int(data.get("other_dependents")) * 500

    fields = {
        "f1_01[0]": data.get("first_name", ""),
        "f1_02[0]": data.get("last_name", ""),
        "f1_03[0]": data.get("address", ""),
        "f1_04[0]": _city_state_zip(data),
        "f1_05[0]": ssn_display,
        "f1_06[0]": _money(children_amt),
        "f1_07[0]": _money(other_dep_amt),
        "f1_08[0]": _money(children_amt + other_dep_amt),
        "f1_09[0]": _money(data.get("other_income", "")),
        "f1_10[0]": _money(data.get("deductions", "")),
        "f1_11[0]": _money(data.get("extra_withholding", "")),
        "f1_12[0]": " — ".join(x for x in (employer["name"], employer["address"]) if x),
        "f1_14[0]": employer["ein"],
    }
    status_field = W4_FILING_STATUS.get((data.get("filing_status") or "").strip())
    if status_field:
        fields[status_field] = True
    if data.get("multiple_jobs") == "check_box":
        fields["c1_2[0]"] = True

    pdf = PdfWrapper(str(W4_TEMPLATE), generate_appearance_streams=True).fill(
        fields, flatten=False
    )
    # Step 5 signature row (no form fields there) — coordinates in PDF points
    # from the bottom-left of page 1, located via generate_coordinate_grid.
    elements = []
    signature = (data.get("signature") or "").strip()
    sign_date = _us_date(data.get("sign_date", ""))
    if signature:
        elements.append(RawText(signature, 1, 150, 110))
    if sign_date:
        elements.append(RawText(sign_date, 1, 475, 110))
    if elements:
        pdf = pdf.draw(elements)
    return pdf.read()


# ── I-9 (Edition 01/20/25 field map, verified visually) ───────────────────────

I9_STATUS_CHECKBOX = {
    "citizen": "CB_1",
    "noncitizen_national": "CB_2",
    "lawful_pr": "CB_3",
    "work_authorized": "CB_4",
}


def fill_i9(data: dict, employer: dict, employee=None) -> bytes:
    status = (data.get("citizenship_status") or "").strip()
    fields = {
        "Last Name (Family Name)": data.get("last_name", ""),
        "First Name Given Name": data.get("first_name", ""),
        "Employee Middle Initial (if any)": data.get("middle_initial", ""),
        "Employee Other Last Names Used (if any)": data.get("other_names", ""),
        "Address Street Number and Name": data.get("address", ""),
        "Apt Number (if any)": data.get("apt", ""),
        "City or Town": data.get("city", ""),
        "ZIP Code": data.get("zip", ""),
        "Date of Birth mmddyyyy": _us_date(data.get("dob", "")),
        "US Social Security Number": _digits(data.get("ssn", "")),
        "Employees E-mail Address": data.get("email", ""),
        "Telephone Number": data.get("phone", ""),
        "Signature of Employee": data.get("signature", ""),
        "Today's Date mmddyyy": _us_date(data.get("sign_date", "")),
        # Section 2 helpers for the admin (recap + org info); employer signs on paper.
        "Employers Business or Org Name": employer["name"],
        "Employers Business or Org Address": employer["address"],
        "Last Name Family Name from Section 1": data.get("last_name", ""),
        "First Name Given Name from Section 1": data.get("first_name", ""),
        "Middle initial if any from Section 1": data.get("middle_initial", ""),
    }
    state = (data.get("state") or "").strip().upper()
    if state:
        fields["State"] = state

    checkbox = I9_STATUS_CHECKBOX.get(status)
    if checkbox:
        fields[checkbox] = True
    if status == "lawful_pr":
        fields["3 A lawful permanent resident Enter USCIS or ANumber"] = data.get(
            "alien_reg_num", ""
        )
    elif status == "work_authorized":
        fields["Exp Date mmddyyyy"] = _us_date(data.get("work_auth_expiry", ""))
        fields["USCIS ANumber"] = data.get("alien_reg_num", "")
        fields["Form I94 Admission Number"] = data.get("i94_num", "")
        fields["Foreign Passport Number and Country of IssuanceRow1"] = data.get(
            "foreign_passport", ""
        )

    # Documents the employee said they'll present (admin verifies in person).
    if data.get("doc_list_a"):
        fields["Document Title 1"] = data["doc_list_a"]
    if data.get("doc_list_b"):
        fields["List B Document 1 Title"] = data["doc_list_b"]
    if data.get("doc_list_c"):
        fields["List C Document Title 1"] = data["doc_list_c"]

    return (
        PdfWrapper(str(I9_TEMPLATE), generate_appearance_streams=True)
        .fill(fields, flatten=False)
        .read()
    )


# ── DE-4 (California EDD 2026 revision — field names verified via schema) ──────
# Place the official form at forms/de4.pdf (edd.ca.gov/pdf_pub_ctr/de4.pdf).
# Re-verify field names after any new EDD revision with:
#   python -c "from PyPDFForm import PdfWrapper; import json; print(json.dumps(PdfWrapper('forms/de4.pdf').schema, indent=2))"


def fill_de4(data: dict, employer: dict) -> bytes:
    if not DE4_TEMPLATE.exists():
        raise FileNotFoundError(
            "DE-4 template not found. Download de4.pdf from "
            "edd.ca.gov/pdf_pub_ctr/de4.pdf and place it in the forms/ directory."
        )

    ssn = _digits(data.get("ssn", ""))
    ssn_display = f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}" if len(ssn) == 9 else (data.get("ssn") or "")

    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    # "Name 1" field description: "First, Middle, Last Name"
    full_name = f"{first} {last}".strip()

    # Worksheet A individual allowance lines
    wa_a = _int(data.get("allowance_personal", 1))
    wa_b = _int(data.get("allowance_spouse", 0))
    wa_c = _int(data.get("allowance_blind_elderly", 0))  # blind-self; form splits self/spouse
    wa_e = _int(data.get("allowance_dependents", 0)) + _int(data.get("allowance_other_deps", 0))
    wa_f = wa_a + wa_b + wa_c + wa_e  # Worksheet A total → feeds line 1a

    # Line 1b comes from Worksheet B (user entered as allowance_deductions)
    line_1b = _int(data.get("allowance_deductions", 0))
    line_1c = wa_f + line_1b  # total allowances

    fields = {
        # Header section
        "Name 1":                   full_name,
        "Social Security Number 1": ssn_display,
        "Address 1":                (data.get("address") or "").strip(),
        "City":                     (data.get("city") or "").strip(),
        "State":                    (data.get("state") or "")[:2].upper(),
        "ZIP Code":                 (data.get("zip") or "")[:5],

        # Filing status checkboxes (mutually exclusive)
        "Filing Status 1": (data.get("filing_status") == "single_dual"),
        "Filing Status 2": (data.get("filing_status") == "married_one"),
        "Filing Status 3": (data.get("filing_status") == "head_of_household"),

        # Allowance lines (front of form)
        "1a": str(wa_f) if wa_f else "",
        "1b": str(line_1b) if line_1b else "",
        "1c": str(line_1c) if line_1c else "",

        # Additional withholding
        "2": _money(data.get("extra_withholding", "")),

        # Exemption checkboxes
        "WHexempt":    (data.get("claim_exempt") == "yes" and data.get("exempt_reason") != "nonresident"),
        "notsubj2WH":  (data.get("exempt_reason") == "nonresident"),

        # Date field (AcroForm — no need for RawText)
        "Date Employee Signed": _us_date(data.get("sign_date", "")),

        # Employer section
        "Employer's Name and Address": " — ".join(
            x for x in (employer.get("name", ""), employer.get("address", "")) if x
        ),

        # Worksheet A detail lines (back of form / overflow section)
        "WKsheetA_A": str(wa_a) if wa_a else "",
        "WKsheetA_B": str(wa_b) if wa_b else "",
        "WKsheetA_C": str(wa_c) if wa_c else "",
        "WKsheetA_D": "",
        "WKsheetA_E": str(wa_e) if wa_e else "",
        "WKsheetA_F": str(wa_f) if wa_f else "",
    }

    # Signature is not an AcroForm field — draw it as text
    pdf = PdfWrapper(str(DE4_TEMPLATE), generate_appearance_streams=True).fill(
        fields, flatten=False
    )
    signature = (data.get("signature") or "").strip()
    if signature:
        pdf = pdf.draw([RawText(signature, 1, 120, 72)])
    return pdf.read()
