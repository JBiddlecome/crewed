from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base


def minutes_between(start: str, end: str) -> int:
    """Minutes between two HH:MM strings; spans midnight if end < start."""
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except (AttributeError, ValueError, TypeError):
        return 0
    mins = (eh * 60 + em) - (sh * 60 + sm)
    if mins < 0:
        mins += 24 * 60
    return mins


def hours_between(start: str, end: str, break_minutes: int = 0) -> float:
    """Hours between two HH:MM strings; spans midnight if end <= start."""
    try:
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except (AttributeError, ValueError):
        return 0.0
    mins = (eh * 60 + em) - (sh * 60 + sm)
    if mins <= 0:
        mins += 24 * 60
    mins -= break_minutes or 0
    return max(round(mins / 60, 2), 0.0)


class Setting(Base):
    __tablename__ = "setting"
    key = Column(String, primary_key=True)
    value = Column(String)


class ClientCompany(Base):
    __tablename__ = "client_company"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String)
    email = Column(String)
    address1 = Column(String)
    address2 = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    industry = Column(String)
    status = Column(String, default="active")   # active | prospect | inactive | terminated
    notes = Column(Text)
    markup_override = Column(Float)  # percent; null = use global setting
    rate_setting = Column(String, default="client_controlled")  # client_controlled | preset_rates
    portal_approved = Column(Boolean, default=True)  # False until admin activates new signups
    gusto_company_uuid = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="company")
    locations = relationship("Location", back_populates="company")
    positions = relationship("ClientPosition", back_populates="company")
    preset_rates = relationship("ClientPresetRate", back_populates="company")
    shifts = relationship("Shift", back_populates="company")
    events = relationship("Event", back_populates="company")


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    role = Column(String, nullable=False)  # admin | client | employee
    status = Column(String, default="active")  # active | pending | disabled
    client_id = Column(Integer, ForeignKey("client_company.id"))
    phone = Column(String)
    address = Column(String)
    city = Column(String)
    state = Column(String)
    zip = Column(String)
    dob = Column(Date)
    gender = Column(String)
    hire_date = Column(Date)
    rehire_date = Column(Date)
    concierge_date = Column(Date)
    payroll_id = Column(String)
    ssn = Column(String)
    interview_notes = Column(Text)
    background_check_date = Column(Date)
    background_check_status = Column(String)  # clean | has_background
    gusto_employee_uuid = Column(String)
    profile_picture = Column(String)
    profile_picture_approved = Column(Boolean, default=False)
    profile_picture_declined = Column(Boolean, default=False)
    resume_file = Column(String)
    resume_text = Column(Text)
    email_confirmed = Column(Boolean, default=False)
    confirmation_token = Column(String)
    reset_token = Column(String)
    reset_token_expires = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("ClientCompany", back_populates="users")
    positions = relationship(
        "EmployeePosition", back_populates="employee", cascade="all, delete-orphan"
    )
    certifications = relationship(
        "EmployeeCert", back_populates="employee", cascade="all, delete-orphan"
    )

    @property
    def name(self):
        return f"{self.first_name} {self.last_name}"


class Location(Base):
    __tablename__ = "location"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    name = Column(String, nullable=False)
    address1 = Column(String, nullable=False)
    address2 = Column(String)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip = Column(String, nullable=False)
    parking = Column(Text)  # default details; overridable per event and per shift
    check_in_location = Column(Text)
    check_in_contact = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("ClientCompany", back_populates="locations")


class LocationDay(Base):
    """Legacy — superseded by Event. Kept so existing data is not lost on startup."""

    __tablename__ = "location_day"
    __table_args__ = (UniqueConstraint("location_id", "date"),)
    id = Column(Integer, primary_key=True)
    location_id = Column(Integer, ForeignKey("location.id"), nullable=False)
    date = Column(Date, nullable=False)
    parking = Column(Text)
    check_in_location = Column(Text)
    check_in_contact = Column(Text)

    location = relationship("Location")


class Event(Base):
    """A booking event — one location on one date, acting as a folder for its shifts.

    Address fields are snapshotted from the Location at creation time so they can
    be edited independently (e.g. the venue changes its entrance) without touching
    the master Location record.
    """

    __tablename__ = "event"
    __table_args__ = (UniqueConstraint("client_id", "location_id", "event_date"),)
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("location.id"), nullable=False)
    event_date = Column(Date, nullable=False)
    # Snapshot address — editable independently of the master Location
    name = Column(String, nullable=False)
    address1 = Column(String, nullable=False)
    address2 = Column(String)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip = Column(String, nullable=False)
    # Operational details — shift-level fields override these
    parking = Column(Text)
    check_in_location = Column(Text)
    check_in_contact = Column(Text)
    notes = Column(Text)
    status = Column(String, default="active")  # active | cancelled
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("ClientCompany", back_populates="events")
    location = relationship("Location")
    shifts = relationship("Shift", back_populates="event")

    @property
    def live_shifts(self):
        return [s for s in self.shifts if s.status != "cancelled"]

    @property
    def headcount(self):
        return sum(s.headcount for s in self.live_shifts)

    @property
    def confirmed_count(self):
        return sum(s.confirmed_count for s in self.live_shifts)


class Position(Base):
    __tablename__ = "position"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    default_pay_rate_l1 = Column(Float, nullable=False, default=0)
    default_bill_rate_l1 = Column(Float, nullable=False, default=0)
    default_markup_l1 = Column(Float, nullable=False, default=0)
    default_pay_rate_l2 = Column(Float, nullable=False, default=0)
    default_bill_rate_l2 = Column(Float, nullable=False, default=0)
    default_markup_l2 = Column(Float, nullable=False, default=0)
    default_pay_rate_l3 = Column(Float, nullable=False, default=0)
    default_bill_rate_l3 = Column(Float, nullable=False, default=0)
    default_markup_l3 = Column(Float, nullable=False, default=0)
    
    # Legacy columns kept to satisfy NOT NULL constraints in existing schema
    default_pay_rate = Column(Float, default=0)
    default_bill_rate = Column(Float, default=0)
    default_markup = Column(Float, default=0)


class Certification(Base):
    __tablename__ = "certification"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class ClientPosition(Base):
    __tablename__ = "client_position"
    __table_args__ = (UniqueConstraint("client_id", "position_id"),)
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    position_id = Column(Integer, ForeignKey("position.id"), nullable=False)
    pay_rate = Column(Float, nullable=False)
    requirements = Column(Text)  # free-text: uniform, experience, etc.

    company = relationship("ClientCompany", back_populates="positions")
    position = relationship("Position")
    certs = relationship(
        "ClientPositionCert", back_populates="client_position", cascade="all, delete-orphan"
    )


class ClientPresetRate(Base):
    __tablename__ = "client_preset_rate"
    __table_args__ = (UniqueConstraint("client_id", "position_id"),)
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    position_id = Column(Integer, ForeignKey("position.id"), nullable=False)
    pay_rate_l1 = Column(Float, nullable=False, default=0)
    bill_rate_l1 = Column(Float, nullable=False, default=0)
    markup_l1 = Column(Float, nullable=False, default=0)
    pay_rate_l2 = Column(Float, nullable=False, default=0)
    bill_rate_l2 = Column(Float, nullable=False, default=0)
    markup_l2 = Column(Float, nullable=False, default=0)
    pay_rate_l3 = Column(Float, nullable=False, default=0)
    bill_rate_l3 = Column(Float, nullable=False, default=0)
    markup_l3 = Column(Float, nullable=False, default=0)
    
    # Legacy columns kept to satisfy NOT NULL constraints in existing schema
    pay_rate = Column(Float, default=0)
    bill_rate = Column(Float, default=0)
    markup = Column(Float, default=0)

    company = relationship("ClientCompany", back_populates="preset_rates")
    position = relationship("Position")
    history = relationship("ClientPresetRateHistory", back_populates="preset_rate", cascade="all, delete-orphan", order_by="desc(ClientPresetRateHistory.changed_at)")


class ClientPresetRateHistory(Base):
    __tablename__ = "client_preset_rate_history"
    id = Column(Integer, primary_key=True)
    client_preset_rate_id = Column(Integer, ForeignKey("client_preset_rate.id"), nullable=False)
    pay_rate_l1 = Column(Float, nullable=False, default=0)
    bill_rate_l1 = Column(Float, nullable=False, default=0)
    markup_l1 = Column(Float, nullable=False, default=0)
    pay_rate_l2 = Column(Float, nullable=False, default=0)
    bill_rate_l2 = Column(Float, nullable=False, default=0)
    markup_l2 = Column(Float, nullable=False, default=0)
    pay_rate_l3 = Column(Float, nullable=False, default=0)
    bill_rate_l3 = Column(Float, nullable=False, default=0)
    markup_l3 = Column(Float, nullable=False, default=0)
    
    # Legacy columns kept to satisfy NOT NULL constraints in existing schema
    pay_rate = Column(Float, default=0)
    bill_rate = Column(Float, default=0)
    markup = Column(Float, default=0)
    
    changed_at = Column(DateTime, default=datetime.utcnow)
    changed_by = Column(Integer, ForeignKey("user.id"), nullable=True)

    preset_rate = relationship("ClientPresetRate", back_populates="history")
    admin_user = relationship("User")


class ClientPositionCert(Base):
    __tablename__ = "client_position_cert"
    id = Column(Integer, primary_key=True)
    client_position_id = Column(Integer, ForeignKey("client_position.id"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certification.id"), nullable=False)

    client_position = relationship("ClientPosition", back_populates="certs")
    certification = relationship("Certification")


class EmployeePosition(Base):
    __tablename__ = "employee_position"
    __table_args__ = (UniqueConstraint("user_id", "position_id"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    position_id = Column(Integer, ForeignKey("position.id"), nullable=False)
    status = Column(String, default="pending")  # pending | approved | declined
    level = Column(Integer, default=2)  # 1 | 2 | 3; set to 2 on AI approval, admin can adjust
    decline_reason = Column(Text)

    employee = relationship("User", back_populates="positions")
    position = relationship("Position")


class EmployeeCert(Base):
    __tablename__ = "employee_cert"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    certification_id = Column(Integer, ForeignKey("certification.id"), nullable=False)
    expires_on = Column(Date)

    employee = relationship("User", back_populates="certifications")
    certification = relationship("Certification")


class MinWage(Base):
    __tablename__ = "min_wage"
    id = Column(Integer, primary_key=True)
    state = Column(String(2), unique=True, nullable=False)
    rate = Column(Float, nullable=False)


class Shift(Base):
    __tablename__ = "shift"
    id = Column(Integer, primary_key=True)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("location.id"), nullable=False)
    event_id = Column(Integer, ForeignKey("event.id"), nullable=True)
    position_id = Column(Integer, ForeignKey("position.id"), nullable=False)
    shift_date = Column(Date, nullable=False)
    start_time = Column(String(5), nullable=False)  # HH:MM
    end_time = Column(String(5), nullable=False)
    headcount = Column(Integer, default=1, nullable=False)
    pay_rate = Column(Float, nullable=False)
    bill_rate = Column(Float, nullable=False)  # snapshot: pay * (1 + markup%)
    notes = Column(Text)
    required_level = Column(Integer, default=1)  # 1 | 2 | 3; minimum employee level required
    parking = Column(Text)  # per-shift override; null = inherit day/location
    check_in_location = Column(Text)
    check_in_contact = Column(Text)
    status = Column(String, default="open")  # open | filled | cancelled | completed
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("ClientCompany", back_populates="shifts")
    location = relationship("Location")
    event = relationship("Event", back_populates="shifts")
    position = relationship("Position")
    assignments = relationship(
        "Assignment", back_populates="shift", cascade="all, delete-orphan"
    )

    @property
    def scheduled_hours(self):
        return hours_between(self.start_time, self.end_time)

    @property
    def confirmed_count(self):
        return sum(1 for a in self.assignments if a.status == "confirmed")

    @property
    def requested_count(self):
        return sum(1 for a in self.assignments if a.status == "requested")


class Assignment(Base):
    __tablename__ = "assignment"
    __table_args__ = (UniqueConstraint("shift_id", "employee_id"),)
    id = Column(Integer, primary_key=True)
    shift_id = Column(Integer, ForeignKey("shift.id"), nullable=False)
    employee_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    status = Column(String, default="requested")  # requested | confirmed | declined | cancelled
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime)

    shift = relationship("Shift", back_populates="assignments")
    employee = relationship("User")
    timesheet = relationship(
        "Timesheet", back_populates="assignment", uselist=False, cascade="all, delete-orphan"
    )


class Timesheet(Base):
    __tablename__ = "timesheet"
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("assignment.id"), unique=True, nullable=False)
    start_time = Column(String(5))
    end_time = Column(String(5))
    break_minutes = Column(Integer, default=0)
    meal_start_time = Column(String(5))
    meal_end_time = Column(String(5))
    billing_start_time = Column(String(5))
    billing_end_time = Column(String(5))
    billing_break_minutes = Column(Integer)
    billing_meal_start_time = Column(String(5))
    billing_meal_end_time = Column(String(5))
    is_disputed = Column(Boolean, default=False)
    dispute_reason = Column(Text)
    is_closed = Column(Boolean, default=False)
    status = Column(String, default="pending")  # pending | submitted | approved
    submitted_at = Column(DateTime)
    approved_at = Column(DateTime)
    approved_by = Column(Integer, ForeignKey("user.id"))
    deleted_at = Column(DateTime)
    deleted_by = Column(Integer, ForeignKey("user.id"))

    assignment = relationship("Assignment", back_populates="timesheet")
    approved_by_user = relationship("User", foreign_keys=[approved_by])
    deleted_by_user = relationship("User", foreign_keys=[deleted_by])
    events = relationship(
        "TimesheetEvent", back_populates="timesheet",
        order_by="TimesheetEvent.occurred_at", cascade="all, delete-orphan"
    )

    @property
    def employee_break_minutes(self):
        if self.meal_start_time and self.meal_end_time:
            return minutes_between(self.meal_start_time, self.meal_end_time)
        return self.break_minutes or 0

    @property
    def client_break_minutes(self):
        if self.billing_meal_start_time and self.billing_meal_end_time:
            return minutes_between(self.billing_meal_start_time, self.billing_meal_end_time)
        if self.billing_break_minutes is not None:
            return self.billing_break_minutes
        return self.employee_break_minutes

    @property
    def employee_hours(self):
        if not self.start_time or not self.end_time:
            return 0.0
        return hours_between(self.start_time, self.end_time, self.employee_break_minutes)

    @property
    def billing_hours(self):
        start = self.billing_start_time or self.start_time
        end = self.billing_end_time or self.end_time
        if not start or not end:
            return 0.0
        return hours_between(start, end, self.client_break_minutes)

    @property
    def hours(self):
        return self.billing_hours


class TimesheetEvent(Base):
    __tablename__ = "timesheet_event"
    id = Column(Integer, primary_key=True)
    timesheet_id = Column(Integer, ForeignKey("timesheet.id"), nullable=False)
    event_type = Column(String, nullable=False)
    occurred_at = Column(DateTime, default=datetime.utcnow)
    actor_id = Column(Integer, ForeignKey("user.id"))
    actor_role = Column(String)  # employee | client | admin | system
    notes = Column(Text)

    timesheet = relationship("Timesheet", back_populates="events")
    actor = relationship("User", foreign_keys=[actor_id])


class Message(Base):
    """Client → employee communication; shows up in the employee's notifications."""

    __tablename__ = "message"
    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    recipient_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    body = Column(Text, nullable=False)
    shift_id = Column(Integer, ForeignKey("shift.id"))
    location_id = Column(Integer, ForeignKey("location.id"))
    context_date = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime)

    sender = relationship("User", foreign_keys=[sender_id])
    recipient = relationship("User", foreign_keys=[recipient_id])
    shift = relationship("Shift")
    location = relationship("Location")


class BlockList(Base):
    __tablename__ = "block_list"
    __table_args__ = (UniqueConstraint("employee_id", "client_id", "location_id"),)
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("location.id"), nullable=True)
    reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])
    client = relationship("ClientCompany")
    location = relationship("Location")


class AList(Base):
    __tablename__ = "a_list"
    __table_args__ = (UniqueConstraint("employee_id", "client_id", "location_id"),)
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    client_id = Column(Integer, ForeignKey("client_company.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("location.id"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("User", foreign_keys=[employee_id])
    client = relationship("ClientCompany")
    location = relationship("Location")


# ──────────────────────────────────────────────────────────────
# Onboarding / Recruiting
# ──────────────────────────────────────────────────────────────

class OnboardingDocument(Base):
    """A PDF document uploaded by an admin for the onboarding package."""
    __tablename__ = "onboarding_document"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    # doc_type: 'pdf' | 'w4_wizard' | 'i9_wizard'
    doc_type = Column(String, default="pdf", nullable=False)
    filename = Column(String)              # stored filename in uploads/onboarding_pdfs/
    description = Column(Text)
    requires_signature = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    fields = relationship(
        "OnboardingField", back_populates="document", cascade="all, delete-orphan",
        order_by="OnboardingField.page, OnboardingField.y_pct"
    )
    package_items = relationship("OnboardingPackageItem", back_populates="document")


class OnboardingField(Base):
    """A positioned input field overlay on a page of an onboarding PDF."""
    __tablename__ = "onboarding_field"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("onboarding_document.id"), nullable=False)
    page = Column(Integer, default=1, nullable=False)    # 1-indexed
    # field_type: 'text' | 'signature' | 'date' | 'checkbox' | 'email' | 'phone' | 'address'
    field_type = Column(String, default="text", nullable=False)
    label = Column(String, nullable=False)               # e.g. "Full Name", "Signature"
    # Position as % of page dimensions (0–100) for resolution independence
    x_pct = Column(Float, nullable=False, default=10.0)
    y_pct = Column(Float, nullable=False, default=10.0)
    w_pct = Column(Float, nullable=False, default=30.0)
    h_pct = Column(Float, nullable=False, default=5.0)
    required = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    document = relationship("OnboardingDocument", back_populates="fields")
    values = relationship(
        "EmployeeOnboardingFieldValue", back_populates="field", cascade="all, delete-orphan"
    )


class OnboardingPackageItem(Base):
    """Ordered list of documents every new employee must complete."""
    __tablename__ = "onboarding_package_item"
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("onboarding_document.id"), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    required = Column(Boolean, default=True)

    document = relationship("OnboardingDocument", back_populates="package_items")


class EmployeeOnboarding(Base):
    """Tracks one employee's progress on one onboarding document."""
    __tablename__ = "employee_onboarding"
    __table_args__ = (UniqueConstraint("employee_id", "document_id"),)
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("onboarding_document.id"), nullable=False)
    # status: 'not_started' | 'in_progress' | 'complete'
    status = Column(String, default="not_started", nullable=False)
    completed_at = Column(DateTime)
    # For wizard types, stores JSON blob of all answers
    wizard_data = Column(Text)

    employee = relationship("User")
    document = relationship("OnboardingDocument")
    field_values = relationship(
        "EmployeeOnboardingFieldValue", back_populates="onboarding_record",
        cascade="all, delete-orphan"
    )


class EmployeeOnboardingFieldValue(Base):
    """The value an employee entered for a specific field on a document."""
    __tablename__ = "employee_onboarding_field_value"
    __table_args__ = (UniqueConstraint("onboarding_id", "field_id"),)
    id = Column(Integer, primary_key=True)
    onboarding_id = Column(Integer, ForeignKey("employee_onboarding.id"), nullable=False)
    field_id = Column(Integer, ForeignKey("onboarding_field.id"), nullable=False)
    value = Column(Text)

    onboarding_record = relationship("EmployeeOnboarding", back_populates="field_values")
    field = relationship("OnboardingField", back_populates="values")


# ──────────────────────────────────────────────────────────────
# Project Management Tickets
# ──────────────────────────────────────────────────────────────

class Ticket(Base):
    __tablename__ = "ticket"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    department = Column(String, nullable=False)
    description = Column(Text)
    priority = Column(String, default="yellow", nullable=False)  # green | yellow | red
    status = Column(String, default="open", nullable=False)      # open | in_progress | done
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TicketDepartment(Base):
    __tablename__ = "ticket_department"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
