# GoLive / Culinary Staffing Platform — Complete Functional Specification

**Purpose:** This document reverse-engineers the existing temp-staffing platform (PHP/Yii2 backend `culinary-be`, React web app `culinary-fe`, React Native/Expo mobile app `culinary-mobile`) so a functionally equivalent application can be rebuilt from scratch in Python with an entirely different UI/UX. It describes every entity, business rule, workflow, calculation, and screen. Implementation technology details of the old system are noted only where they reveal business behavior.

**Rebuild scope notes (from the product owner):**
- The new app does NOT need an internal payroll system. It must produce **reports/exports** that (a) let staff create invoices and (b) produce files uploadable to an external payroll system.
- A new **HR / job application module** (employees apply and complete applications fully in-app) will be added later; this spec marks the integration points the old system used for hiring data so the new module can slot in.
- UI/UX must be entirely new; only the *functionality* carries over.

---

## Table of Contents

1. [System Overview & Architecture](#1-system-overview--architecture)
2. [Users, Roles & Permissions](#2-users-roles--permissions)
3. [Global Setup / Reference Data](#3-global-setup--reference-data)
4. [Client Domain](#4-client-domain)
5. [Venue Domain](#5-venue-domain)
6. [Position & Rate System](#6-position--rate-system)
7. [Employee Domain](#7-employee-domain)
8. [Events & Shifts](#8-events--shifts)
9. [Shift Publishing (Broadcast/Matching Engine)](#9-shift-publishing-broadcastmatching-engine)
10. [Shift Request / Confirmation Workflow](#10-shift-request--confirmation-workflow)
11. [Timesheets](#11-timesheets)
12. [State Labor Rules: Meal Breaks, Penalties, Minimum Billing](#12-state-labor-rules-meal-breaks-penalties-minimum-billing)
13. [Overtime Engine](#13-overtime-engine)
14. [Pay & Bill Calculation (per shift)](#14-pay--bill-calculation-per-shift)
15. [Payroll Module, Reports & Invoicing](#15-payroll-module-reports--invoicing)
16. [Notifications, Email, SMS, Push](#16-notifications-email-sms-push)
17. [Scheduled Automation (Cron Jobs)](#17-scheduled-automation-cron-jobs)
18. [Admin Web Application — Screen Inventory](#18-admin-web-application--screen-inventory)
19. [Client Portal — Screen Inventory](#19-client-portal--screen-inventory)
20. [Employee Web & Mobile App — Screen Inventory](#20-employee-web--mobile-app--screen-inventory)
21. [Registration & Invitation Flows](#21-registration--invitation-flows)
22. [External Integrations](#22-external-integrations)
23. [Change History / Audit System](#23-change-history--audit-system)
24. [Rebuild Guidance & HR Module Integration Points](#24-rebuild-guidance--hr-module-integration-points)
25. [Appendix A — Full Data Dictionary](#25-appendix-a--full-data-dictionary)

---

## 1. System Overview & Architecture

The platform operates a temp-staffing agency for hospitality/culinary work. The data flow at the highest level:

```
Clients (companies) ──┬─ have Venues (work locations)
                      ├─ have Client Positions (job types + pay/bill rates)
                      └─ have settings (billing, deadlines, requirements)

Venues ── have Venue Positions (overrides of client positions per location)

Events (a date at a venue) ── contain Shifts (start/end time)
Shifts ── contain Shift Positions (position + headcount + rates + requirements)

Publishing ── broadcasts open shift positions to eligible employees
Employees ── request (apply for) shift positions; admins/clients approve
ShiftEmployee ── the assignment record (request → confirm → work/cancel)
Timesheet ── auto-created when a ShiftEmployee is confirmed; records hours

Payroll ── aggregates timesheets → employee pay & client billing reports/invoices
```

Three user-facing applications share one REST API:

| App | Audience | Function |
|---|---|---|
| Admin web app | Agency staff (admins, staffing managers, payroll, HR, recruiters, sales) | Full management of everything |
| Client portal (web + mobile) | Client company users | View/edit own profile, venues, create events, request/approve employees, fill client-side timesheets, DNR/preferred lists |
| Employee app (mobile-first + web) | Field employees | See published shifts, apply, manage profile/certifications, clock in/out, submit timesheets |

The API is versioned by audience: `v1/*` (admin + employee), `client/*` (client portal, with a nested `venue` module), plus an OAuth2 token endpoint (password grant, bearer tokens, ~25-day lifetime, no refresh token). Locale is selected via an `X-Locale` header (i18n built in; English + Spanish translations exist).

Soft-delete is pervasive: most entities have `deleted_at`/`deleted_by` and are filtered out of queries rather than removed.

Nearly all create/update operations record who did them (`created_by`, etc.) and many write **HistoryEntry** audit rows (see §23).

---

## 2. Users, Roles & Permissions

### 2.1 User accounts (`user` table)

One `user` record per login. Key fields: `username`, `email`, `phone`, `password_hash`, `first_name`, `last_name`, `timezone`, `group`, `role` (legacy), `status` (0 deleted / 1 created / 10 active), `last_logged_in`, `terms_accepted`, `settings` (JSON of notification preferences), `verify_token`/`email_verified` (onboarding), `test` flag, soft delete.

A user is linked to its “party” via **UserParty** (`user_id`, `employee_id`, `client_id`): an employee user points to an Employee record; a client user points to a Client record. Multiple users can belong to one client (client staff logins). A user could in principle have multiple parties (the client mobile app has a "select company" screen when a user belongs to multiple clients).

### 2.2 Groups and roles

`user.group` is one of: **OWNER, ADMIN, EMPLOYEE, CLIENT**.

RBAC roles (Yii RBAC tables `auth_item`, `auth_assignment`, etc.):

| Role | Label | Notes |
|---|---|---|
| owner | Owner | superuser |
| superAdmin | Super Admin | can edit closed timesheets |
| admin | Admin | general agency staff |
| payroll | Payroll | payroll section; can edit closed timesheets |
| humanResource | Human Resource | HR exports, employee files |
| salesCoordinator | Staffing Coordinator | |
| salesExecutive | Sales Executive | assignable to clients (`sales_executive_id`) |
| staffManager | Staffing Manager | assignable to venues (`staffing_manager_id`); receives most operational emails |
| recruiter | Recruiter | assignable to employees (`recruited_by`) |
| employee | Employee | field worker |
| client / clientAdmin / clientBasic / clientOwner / clientOnsiteManager | Client portal tiers | granular permissions below |

Client-portal granular permissions (assigned per client role): view profile; view event; view/update/delete contacts; view/update/delete DNRs (own vs any); view/update/delete preferred list (own vs any); view/update/delete documents; view/update/delete users; view/update timesheets; view/update venues; view/update/delete venue positions; view shift clock codes; update/delete shifts (`clientModifyShiftWhen` time-window rule); `deleteShiftWhen` rule for admins.

### 2.3 User settings (notification preferences, JSON on user)

Per-user toggles, grouped on a Settings screen: disable all email notifications; disable all reminder notifications; publish email; publish email for ineligible positions; publish push notification (+ ineligible variant); request-shift email; confirm-shift email; event-change email; message-employees email; 1st/2nd/3rd shift reminder; shift cancelled <24h notification; shift confirm <24h notification; show locations with background requirements.

---

## 3. Global Setup / Reference Data

All managed under the admin **Setup** area. Each item is created/edited by admins and referenced throughout. Unless noted, records have `id`, `name`/label, `created_at/by`, soft delete.

| Entity | Fields & behavior |
|---|---|
| **Position** (master catalog) | `description` (name), `group_id`, default `rate`, `visible_when` (visibility rule), `disable_timeclock_code` (this position never uses clock codes — e.g., supervisors), linked default uniforms / tools / grooming tools / certifications (junction tables `position_uniform`, `position_tool`, `position_grooming_tool`, `position_certification`), optional `PositionMaterial` (free-text required materials), `PositionSubType` (sub-titles). |
| **Uniform** | `name`, `images` (JSON), `questions` (JSON; attestation-style questions about owning the uniform). |
| **Tool** | `name`. |
| **GroomingTool** (grooming standard) | `name`. |
| **Certification** | `name`, `required` (`ALL_CLIENTS` / `AS_SPECIFIED`), `document_required`, `number_required` (cert #), `issued_at_required`, `can_expire`, `approval_required` (admin must approve uploads), `has_effective_date`, `effective_date`, `has_max_allowed_months` (validity cap), `minimum_days_of_hire`, `minimum_days_of_1st_shift` (grace periods), `instructions` (rich text shown to employee), `reportable`, `set_it_on_profile`, `other_work_type_id` (paid training type that grants it), `state` (only required in CALIFORNIA/NEVADA), `visible_when`. |
| **Language** | `language`, `preferred` flag. |
| **MinWageRate** + **MinWageRateAmount** | A named wage region (`description`, `default` flag) with date-ranged amounts (`rate`, `start_date`, `end_date`, `note`). Assigned to clients, venues and employees (work region). |
| **WcCode** (workers'-comp code) | `wc_code`, `rate`, `state`. Assigned to clients; used in sick-wage records. |
| **LateFeePolicy** | `name`; attachable to clients (many-to-many `client_late_fee`). |
| **StatusReason** | typed reason catalog: HIATUS, TERMINATED, DNR, DNR_PARENT (hierarchical DNR reasons via `parent_id`), OTHER_STATUS, EVENT_CANCELLATION, EMPLOYEE_CANCEL; fields `type`, `reason`, `visible_when`. |
| **Division** | `name`; client grouping with markup implications (selected per client). |
| **Msp** (Managed Service Provider) | `name`, `rate` (markup %), `penalty_formula` (`BILL_RATE` or `PAY_RATE_BY`), `penalty_formula_multiplier` — MSPs can override how meal-penalty bills are computed (pay rate × multiplier instead of bill rate). MSP also issues per-employee clock codes (`employee_clock_code`: msp_id + employee_id + code) for external timeclock reconciliation. |
| **NetTermsEntry** | `net_terms` text (e.g., “Net 30”); selected per client. |
| **BillingType** | `name`; selected per client (categorizes billing). |
| **DocumentType** | `label`, `type` (`CLIENT_VENUE` or `EMPLOYEE`), `reportable`, `visible_when` (who may view). Used by client/venue/employee documents and document exports. |
| **AttestationQuestion** | `question` text, `first_shift` flag (ask only on an employee’s first shift). Linked to clients/venues via `venue_attestation_question` (client_id + optional venue_id). Employees must answer at confirm/clock-in; answers recorded. |
| **AdditionalShiftPay** | Flat extra daily pay: `name`, `rate`, `start_date`, `end_date`. Summed for any worked shift on a date in range (e.g., COVID hero pay). Affects pay and OT blended rate. |
| **TravelRate** | Date-ranged per-mile rates: `pay_rate`, `bill_rate`, `note`, `start_date`, `end_date`. Used when a shift position has `miles_apply` and employee enters travel distance. |
| **Holiday** | `name`, `date`; attached to clients via `client_holiday` (which holidays a client observes → holiday rates). |
| **Parking** | `name` (parking situation catalog for events). |
| **OtherWorkType** | Non-shift paid work (orientation, training): `name`, `description`, `work_hours`, `non_work_hours`, `rate` mode (`91_DAYS_SHIFTS` = avg rate of shifts in last 91 days, `MINIMUM_WAGE`, `CUSTOM`), `custom_rate`, `cost`, `cost_description`. Granting a cert can be tied to one. |
| **RegistrationPosition** | Public-signup position choices, each mapping to 1+ real positions (`registration_position_position`). |
| **DefaultEmployeePosition** | Positions auto-added to every new employee (`position_id`, `eligible` flag). |
| **SystemNotificationAddress** | Routing of system emails: `notification` key, `email`, `options` — configurable recipient list per notification type. |
| **Preference** (singleton) | Legacy global notification emails: employee submit/change/status, client notification, timesheet notification, suspension, DNR, hours, FH expiry, CC expiry, resignation, 30/60-day inactivity, onboarding docs CC, employee photo changed. |
| **Tutorial** | In-app help: `title`, rich `content`, `interface` (EMPLOYEE_MOBILE/WEB, ADMIN_*, CLIENT_*), `categories`, `tags`, `status` (DRAFT/ACTIVE/ARCHIVED), `popup` (e.g., FIRST_LOGIN), `skippable`; `TutorialSeen` per user. |
| **CompanyInfoItem** | Employee/client handbook articles: `title`, `body`, `interface` (EMPLOYEE/CLIENT), `status`, `weight` (order). Older singleton `CompanyInfo` held fixed handbook sections (office hours, call-out policy, meal periods, attendance, uniforms, pay day, etc.). |
| **Templates** (singleton) | Email templates for shift invite / confirmation / change / removed (subject + body). |
| **BlockedEmail** | Emails banned from registering. |
| **County / State** | US geography reference; events/venues/employees can carry a county. |

---

## 4. Client Domain

### 4.1 Client profile (`client`)

Identity & contact: `name`, `invoice_name` (used on invoices if set, max 41 chars), `other_id` (external id), Salesforce ids (`sf_id`, `sf_user_id`), address1/2/3, city, state (default CA), zip, phone, fax, generic `contact`/`email`, geocoded `latitude`/`longitude` (auto-geocode on address change via Google Maps).

Classification: `status` — Active(1), Prospect(3), Candidate Partner(4), Inactive 60(10), Inactive 180(11), Inactive 365(12), Terminated(0). `industry` — Hotel/Resort, Private Club, Entertainment Studio, Stadium, Convention Center, Corporate Dining, Catering Company, School/University, Hospital/Healthcare, Senior Assisted Living, Rehabilitation Center, Restaurant, Casino, Private Event, Production, Candidate Referral Partner, Other (+`industry_other` text). `division_id`, `msp_id`, `sales_executive_id` (a User), `staff_id` (owning staff member), `won_date`, `msa_expiration` (contract expiry; UI shows warnings: expired / expires today / expires in <31 days).

Billing & finance: `payment_type` (1 Credit Card / 2 Invoice), credit-card expiry fields + `credit_card_authorization_date`, `exposure_limit`, `pay_notes`, `net_terms_entry_id`, `billing_type_id`, `invoice_no`, `invoiced`, `discount` + `discount_vaild_date`, `markup` (text), `wc_id` (workers' comp), `min_wage_id` (wage region), `late_fees` (policies via junction), `bundle` (feature-bundle key, e.g. enables UCLA-specific reports).

**Invoice grouping — `separate_venue`** (drives invoice exports, see §15.4):
- 0 = No (one invoice per client)
- 1 = Invoice venues separately (venue = invoice line grouping)
- 2 = Venues are separate customers
- 3 = Invoice venues separately **and by day**
- 4 = Venues are separate customers **and separated by day**
- 5 = Invoice separately **by PO number**

`invoices_offset` (int days): shifts the date range when pulling this client's invoices (e.g., client's billing week differs from agency's). Helper: date ± offset days.

Scheduling/cancellation policy:
- `cancellation_deadline` (hours) — **bill** deadline: if a confirmed shift is cancelled/changed when shift start ≤ now + deadline hours, client owes minimum billing.
- `cancellation_deadline_pay` (hours) — same but for **employee minimum pay**.
- `surcharge_deadline` (hours) — bookings made inside this window of shift start get the position's surcharge applied to bill rate (last-minute surcharge).
- `auto_confirm` — 0 Disabled / 1 Based on Work / 2 All (see §10.3).
- `no_break_penalty` — 1 Enabled / 2 Disabled: whether client is billed meal-break penalties (copied onto each event at creation).
- `cancellation deadlines` are evaluated by the DB function `EVENT_IN_DEADLINE` (uses the larger of the two deadlines, first confirmed shift start, NOW).

Employee-requirement settings: `background` (1 = accepts NO backgrounds, 2 = accepts specified backgrounds only, 3 = accepts all), with `client_background` junction listing accepted Background types; `background_query_pending` (allow employees with pending checks); `visible_tattoos_allow`; `c19_vaccine` (vaccination required); client-level certifications (`client_certification`), attestation questions; `proceed` flag.

Misc: `payroll_hours_reminder_enabled` (include in payroll hour-reminder emails), `overtime_calculator` (state override for OT calc), `separate_venue`, `workers_comp` text, `created_by`, soft delete.

Behavior on save: new active client → notification email to configured address; status change inactive→active → notification email; address change → re-geocode; creating an event within 60 days for an Inactive 60/180/365 client flips it back to Active automatically.

### 4.2 Client sub-entities

- **ClientContact**: `name`, `title`, `email`, `phone`/`mobile`/`office` + extensions, `preferred_method`, `preferred` flag, `timesheet` (receives timesheets), `invoicing` (receives invoices), `accounts_receivable`, `sort_order`. Contacts can be attached to venues (`venue_contact` with its own `preferred` flag). A contact can be invited to become a client portal user.
- **ClientNote**: free notes with author + date.
- **ClientDocument**: uploaded file + description + DocumentType; soft delete.
- **ClientHoliday**: which global holidays this client observes (holiday rate billing).
- **ClientLateFee**: attached late-fee policies.
- **ClientMultiplier**: date-ranged OT/DT bill multipliers (`ot`, `dt`, `start_date`, `end_date`) — overrides the default 1.5×/2.0× when billing overtime to this client.
- **ClientPurchaseOrder**: `value` (PO number), optional `venue_id`, date range. The active PO for an event date is stamped onto timesheets (`client_po`).
- **Dnr** ("Do Not Return"): employee blocked for client (optionally only one venue): `employee_id`, `client_id`, `venue_id?`, effective `date`, `reason_id` (StatusReason of type DNR, hierarchical), `other_reason`, `notes`, `notification_sent`. DNR’d employees are excluded from publishing and cannot apply; admins get notification emails.
- **Exclusive** (= "Preferred" list): employee preferred for client (optionally venue-specific): `employee_id`, `client_id`, `venue_id?`, `reason`, `notes`. Used by "publish to preferred employees".
- **Client users**: UserParty links; invitation workflow (§21).

---

## 5. Venue Domain

A **Venue** is a physical work location belonging to a client.

Fields: `client_id`, `name`, `invoice_name`, `venue_code`, `photo` + gallery (`venue_picture`), address (+geocoding), `phone`, `county_id`, `status` (active/inactive), `financed`, `factored` (invoice factoring flag — drives Factored/Non-Factored invoice reports), `send_timesheet` flag.

Operational defaults (used to prefill events): `admin_notes`, `venue_details`, `description`, `directions`, `check_in` instructions, `background_requirements` text, `uniform_requirements` text, `parking` mode (`parkings` id: Street / Free on site / Validated / Reimbursed on site / Reimbursed by agency / Not provided), `parking_note`, `free_parking`, `parking_charge`, `parking_reimbursement`, `travel_charge` (bill), `travel_pay` (employee), `service_charge` (flat venue service charge added to bill).

Staffing: `staffing_manager_id` (User: receives no-show/missed-event/timesheet-notes/publish notifications for this venue), `sales_rep_id`.

Timeclock defaults (copied to events): `timeclock` mode, `timeclock_code_holder`, `timeclock_tolerance` (minutes), `timeclock_prestart_interval` (minutes before shift start that clock-in is allowed), `timeclock_limit`; `min_wage_id` (region override).

Venue sub-entities mirroring client ones: VenueContact (links client contacts), VenueDocument (+DocumentType; attachable to events), VenueLanguage (default working languages), VenueCertification (cert requirements with `effective_date`, `max_allowed_months` overrides), VenueAttestationQuestion, venue-level DNRs and Preferred entries (same `dnr`/`exclusive` tables with venue_id), VenuePosition (§6).

---

## 6. Position & Rate System

Three layers; each lower layer overrides/instantiates the one above:

1. **Position** (global catalog) — name, default uniforms/tools/grooming/certs.
2. **ClientPosition** — a position enabled for a client. Carries its own uniform/tool/grooming/cert requirement sets (junctions `client_position_*`) and **date-ranged rate history**: `ClientPositionAmount` rows (`pay_rate`, `bill_rate`, `surcharge` %, `note`, `start_date`, `end_date`). Rate lookup = the row covering the queried date (null-bounded ranges allowed). Surcharge multiplier = `1 + surcharge/100`. Batch rate-change tooling exists (apply % or $ increases to many positions at once, scheduled by date).
3. **VenuePosition** — a position enabled at a venue (overrides client position when present): own description, uniform/tool/grooming/cert sets, and `VenuePositionAmount` date-ranged rates (same shape). UI shows "rate changes" history.

When building a shift, the picker offers the venue's positions (falling back to client positions); the shift position copies the rates and requirement sets current **as of the event date**, including surcharge if booked within the client's `surcharge_deadline`.

---

## 7. Employee Domain

### 7.1 Employee profile (`employee`)

Identity: `first_name`, `last_name`, `dob` (+separate `dob_month`/`dob_day`), `sex` (male/female/non-binary), `email`, `mobile`/`home`/`work` phones, address (+geocode), `county_id`, `photo` (admin-approved; see Action Center), `language` + `employee_language` set (with preferred flag), `ssn` (encrypted/confidential), `payroll_id` (external payroll system id — **key field for payroll export**), `region`, `min_wage_id` (work region/state for wage & OT rules).

Status: `status` — Active(1), Candidate(2), Hiatus(3), Terminated(5), Resigned(6), Inactive 60(10), Other(14), IFR(15); plus `hr_status`, `status_reason`, `other_status`, `terminated_at`, `activated_at`, `flag` (color flags: orange/red/green/brown/blue/purple/yellow), `start_date` (hire), `start_date2` (rehire), `concierge_date`.

Work area (radius matching): `work_area_address…`, `work_area_latitude/longitude`, `work_area_unit` (mi/km), `work_area_distance` (max distance willing to travel), `work_area_review` flag. **Publishing only matches employees whose work-area circle covers the event location** (Haversine distance SQL).

Hiring/application data (the old paper/web application — superseded by your future HR module): how-heard flags, expected rate, smartphone, transportation (+other), referred_by/referred_date, interview date/notes/interviewer, application file, resume, worked_before, work_eligibility, applied_before/date, currently_employed, contact_employer, provide_docs, education history (HS/college/other: name, study, years, graduated, degree), tips/servsafe/CA-food-handler certs flags, capable/accommodations, garnishment, confidential notes, orientation schedule/complete dates, i9_completed, uniforms_complete/missing, `fhc`/`fhc_expiry` (food handler card), `pp_mailing_list` (paper paycheck list), `recruited_by`.

Requirements state: `background` (1 clean / 2 specified) + `employee_background` junction (which Background types the employee has — FELONY/MISDEMEANOR severity catalog), `background_date`, `background_query` (0 none / 1 requested / 2 pending) — admin can request a background check; employee answers; `c19_vaccine`, `restrict_to_exclusive` (may only work clients where preferred), `send_emails`, `std_pay_rate`, `expected_rate`.

### 7.2 Employee sub-entities

- **EmployeePosition**: positions the employee can work: `position_id`, `sub_type_id`, `level` (skill rating 1–3, used as publishing filter), `rate` override, `status` (1 visible / 2 hidden / 3 disabled), `eligible` flag (eligible = fully approved to be notified for this position). Defaults seeded from DefaultEmployeePosition.
- **EmployeeCertification**: per-cert record: `file` upload, `number`, `issued_at`, `expires_at`, `approved_at`/`approved_by` (when cert requires approval; Action Center queue), created tracking. Eligibility rule for a shift = all required certs present, approved (if approval required), not expired at event date, within `max_allowed_months` of `issued_at`/`effective_date` where applicable, honoring `minimum_days_of_hire`/`minimum_days_of_1st_shift` grace windows and state scoping.
- **EmployeeUniform / EmployeeTool / EmployeeGroomingTool**: what the employee owns/complies with. On applying to a shift with uniforms they don't have, the system auto-adds them and stores `attest_uniforms` (they attested they'll have them).
- **EmployeeDocument** (files w/ description) and **EmployeeGovernment** (typed government docs via DocumentType EMPLOYEE).
- **EmployeeNote**: typed notes — DAILY, PERSONNEL, SHIFT_REMOVAL, PAYROLL, SHIFT, HR_DAILY; optional link to a shift_employee. Payroll notes surface on payroll grids/exports.
- **EmployeeOtherWork**: non-shift paid items (orientation/training): type, `date`, `work_hours`, `non_work_hours`, `rate` (resolved per OtherWorkType mode), `cost`, `notes`. Appears in payroll “Other Works” tab and exports.
- **EmployeeClockCode**: per-MSP external timeclock code (importable in bulk).
- **HiatusEmployee**: hiatus history (reason + date). **SickWagePay/SickWageEmployee**: sick/wage-replacement payments (type 1 sick, 2 wage replacement; week range, hours, pay rate, WC code) feeding Sick Pay and Wage Replacement reports.
- **EmployeeAvailable**: calendar availability rows (employee marks dates available/unavailable).
- **EmployeeProfileUpdates**: field-level audit of profile changes (old/new values, who, when).
- **Health screenings** (COVID-era): `health_screening` catalog + per-client requirement + per-employee submission with approval; mobile app screen exists.
- **Calendar**: employee personal blocked/available time rows (start/end).

### 7.3 Employee lifecycle automation

- Inactivity: cron warns then auto-moves employees to Inactive 60 when not working (configurable notification email), with notification emails at 30/60 days.
- Termination/hiatus/DNR set via status + StatusReason; DNR creation can notify staff and removes the employee from all future shifts at that client/venue (see `removeFromFuture` §10.6).
- Background reset/reminder crons; certification-expiration reminder cron (notifies employees of upcoming expiry); welcome email cron for newly activated employees; clock-in reminder cron; auto-NOSHOW cron (marks timesheets NOSHOW when employees never clocked in).

---

## 8. Events & Shifts

### 8.1 Event (`event`) — one venue, one date

Created from the calendar (admin or client portal). Fields:

- `client_id`, `venue_id`, `date`, optional `title` (falls back to venue name; display title = "Title on Weekday, Month D, YYYY"), `invoice_label` (suffix on invoice line).
- Location snapshot: `address1/2`, `city`, `state`, `zip`, `county_id`, lat/long (geocoded). Event state drives which **state labor rules** apply.
- Logistics (prefilled from venue, editable per event): `description`, `venue_details`, `admin_notes`, `directions`, `parking` (+`parking_note`, `parking_reimbursement`), `check_in`, `background_requirements`, `documents` (JSON) + `EventDocument` rows (own uploads or links to venue documents; visibility honors DocumentType permissions), legacy `document1-3`/`description1-3`.
- Money: `travel_charge` (client billed travel, falls back to venue), `travel_pay` (employee travel), `purchase_order` (cascades to shifts without their own PO), `no_break_penalty` (copied from client at insert).
- Flags: `emergency`, `verbal_timesheet`, `timesheet_received`, `staff_count_visible` (clients can see staffing counts), `no_employees`.
- **Timeclock config** (prefilled from venue): `timeclock` ∈ CLIENT, CLIENT_EMPLOYEE, EMPLOYEE, EMPLOYEE_CODE, DISABLED, or external systems KRONOS / ATLAS / NOWSTA (external ⇒ timesheets locked in-app; message "use the {system} system"); `timeclock_code_holder` ∈ EVENT / SHIFT / SHIFT_POSITION / SHIFT_EMPLOYEE (at which level 4-digit clock-in/out codes are generated); `clock_in_code`/`clock_out_code` (random 4-digit, auto-generated at the configured holder level, code lookup walks up the chain employee→position→shift→event); `timeclock_tolerance` (max minutes between claimed start and verified scan before flag), `timeclock_prestart_interval`, `timeclock_limit`.
- Denormalized stats (recomputed on changes; power calendar badges and client staffing visibility): per-event counts of shift positions, openings, filled, confirmed-filled, waiting-for-admin, waiting-for-employee, published, publish-scheduled, employees, employees-confirmed, plus booleans `stat_filled`, `stat_filled_confirmed`.

Event rules: date change is forbidden once any timesheet has time entries (otherwise timesheet datetimes are re-dated); changing travel pay/charge syncs all confirmed employees' timesheets; events can be duplicated (EventDuplicateForm — copies shifts/positions; history records it); soft-deleting an event cascades soft-delete to shifts→positions→assignments (and their timesheets); editing after timesheets were "sent" resets the sent status. Event cancellation records exist (`event_cancellation` w/ StatusReason type EVENT_CANCELLATION + minimum-pay flag).

### 8.2 Shift (`shift`) — a time window in an event

`event_id`, `start`, `end` (datetime; end > start), `old_start`/`old_end` (kept when times change — minimum billing uses the **original** scheduled hours), `purchase_order` (overrides event PO), own clock codes when holder=SHIFT.

Changing a shift's time puts all its non-cancelled employees **back into request state** (unconfirmed); if the change happens inside the client's cancellation deadline, each existing timesheet is marked CANCELLED with min-bill/min-pay set (T1323 rule), then the employee must re-confirm.

### 8.3 ShiftPosition (`shift_position`) — an opening within a shift

`shift_id`, `position_id`, `additional_title` (custom suffix), `sub_type_id`, `gender` preference, `count` (number of openings), `backup` (standby flag), `code` (external/import code), `position_description`, requirement sets (uniform/tool/grooming/cert junctions copied from venue/client position at creation, editable per shift), `miles_apply` (travel distance reimbursement applies — makes employee travel-distance entry required on timesheet).

Money: `rate` (employee pay), `bill_rate`, `base_rate`/`base_bill_rate` (originals before holiday/surcharge adjustments), `holiday_rate` flag (this shift bills/pays holiday premium; UI doubles displayed rate), `surcharge` flag + `surcharge_value` (late-booking surcharge), `bonus` (flat per-shift bonus paid to employee when worked).

Fill tracking: `filled` (count satisfied by non-cancelled assignees), `was_filled` (times it has ever become filled — used to label re-opened shifts "Now Open Last Minute-Emergency"), `was_published` (0 no / 1 init / 2 done). When rates change on a shift position, all live assignments' `rate`/`bill_rate` are updated. When un-filled again, prior soft-cancelled requests are purged except prohibitive ones (so employees can re-apply, but declined-by-admin etc. cannot).

Attestation questions for an employee = venue's questions, minus `first_shift`-only ones if the employee already worked >1 shift (counting from rehire date if present).

---

## 9. Shift Publishing (Broadcast/Matching Engine)

Publishing makes open shift positions visible/applicable to matching employees (and optionally notifies them).

**Publishing** record: `event_id`, `client_id`, shifts subset (`publishing_shift` rows; empty = all), audience `to` ∈ ALL / PREFERRED / WORKED_BEFORE, `level` (employee position level filter), `gender` (both/male/female), languages (`publishing_language`), `employee_statuses` (JSON; default Active + Inactive60 + Candidate; optionally Resigned w/ `notify_resigned`), `begin` (scheduled processing time — can be in the future), `processed` timestamp, soft delete. Created by admins or client users (client-created publishing notifies the venue's staffing manager).

A cron processes due publishings (queue, max parallelism). **Matching** (per open, unfilled shift position):

1. Employee not soft-deleted, status in allowed set, has user account.
2. NOT already on this shift position (with non-cancelled or prohibitive-cancelled record).
3. NOT already confirmed on any shift that same date.
4. Has the position in `employee_position` with `status=VISIBLE` (+level match if filtered).
5. Has a defined work area whose radius covers the event coordinates.
6. Not DNR'd for this client (global or this venue) as of the event date.
7. Audience filter: WORKED_BEFORE → has worked this client before; PREFERRED → on client/venue preferred list.
8. Gender/language filters if set.
9. Passes the client's background acceptance rules.

Each match creates a **PublishEmployee** row (event, shift position, employee, publishing id) = "this shift appears in this employee's available-shifts list". Notification logic: skipped entirely if processing starts >12h after `begin`; Candidates are listed but never notified; Resigned notified only if `notify_resigned`; per-user publish email/push settings honored, including the "ineligible" variants (notify even when employee lacks certs — otherwise only employees holding required certs are notified); PREFERRED publishes notify **immediately** with subject "Preferred Position – URGENT RESPONSE NEEDED"; same-day publishes notify immediately; others are queued (`NOTIFY_WAITING`) and sent by the notification cron; re-opened positions get "Now Open Last Minute-Emergency …" subject, first-time ones "Last Minute-Emergency …". Email + Expo push, via queue jobs. A `debug_publishing` table logs skip reasons.

Deleting/expiring a publishing removes its PublishEmployee rows (employees no longer see the shift). An admin can also **directly request** specific employees (creates ShiftEmployee in request state, §10.2) regardless of publishing.

---

## 10. Shift Request / Confirmation Workflow

### 10.1 ShiftEmployee (`shift_employee`) — the assignment record

Keys: `shift_position_id`, `event_id`, `employee_id`. Lifecycle fields: `confirmed` (0/1), `confirmed_at`, `confirm_type` (1 by employee / 2 phone / 3 email / 4 other / 5 text), `confirm_notes`, `confirmed_by`, `request_by` (who initiated — employee=apply, admin/client=invite), `approved_by`, `remove_by`, `employee_remove_date`, `cancelled_at`, `cancelled_in_deadline`, `read_notification`, `note_to_employee`.

Money snapshot: `rate`, `bill_rate` (copied from shift position; kept in sync on position rate edits), `emergency_rate` flag + `emergency_rate_amount`, `overtime` (JSON calculation result), `overtime_type`, `overtime_paid_by` (CLIENT = billed to client / AGENCY = absorbed), `overtime_reason`, `shift_type` (1 double shift / 2 seven-days / 3 over-40), `hiatus`/`hiatus_reason`, `attest_uniforms` (JSON), own clock codes (holder=SHIFT_EMPLOYEE), `daily_report_notes`.

**Cancel reasons** (`cancel_reason`): 0 active; 2 Employee Cancelled <24h; 3 Employee Cancelled >24h; 4 Client Cancelled Shift; 5 Client Decreased Staff; 6 Employee Cancelled within Policy; 7 Employee Moved to Another Shift; 8 Employee Not Qualified; 9 Accidental Sign-Up; 10 Other; 11 Employee Declined; 12 Declined By Admin; 13 Shift request not confirmed; 14 No Call No Show; 15 DNR; 41/51 "had cancelled/decreased" (historical markers — when a client cancels then re-books the same employee on the same event, the old row is re-tagged so minimum-bill reports stay correct). Employee-side cancel reason text is a separate dictionary (`employee_cancel_reason` + `cancel_notes`).

**Prohibitive reasons** (block re-applying to that position): 8, 14, 15, 2, 12.

### 10.2 Employee applies (ApplyShiftForm) — eligibility gates

Mutex-locked, transactional. All must pass: event date not past **and** position was published to this employee; employee status active-ish; background check passes client rules (pending check → explanatory error); not DNR'd; no existing active/prohibitive record on the position (specific errors: already confirmed / already requested / "we already requested you, please confirm"); position not already filled; holds all required certifications (else lists the missing ones); not already **in request** elsewhere the same day (must resolve first); no time **overlap** with any assignment ±1 day. On success: ShiftEmployee created (request state, `request_by`=employee, rates copied); missing uniforms auto-added to profile + recorded in `attest_uniforms`; attestation questions recorded.

### 10.3 Auto-confirm

If client `auto_confirm` = ALL → instantly confirmed. If BASED_ON_WORK → confirmed only when **all** of: shift starts >24h away; employee has no other confirmed shift that date; employee has a COMPLETED, worked timesheet at this **venue** completed >24h ago; and adding this shift creates **no client-billed overtime**. Auto-confirms write a history entry.

### 10.4 Confirm / decline / cancel

- **Admin/client confirm** (ConfirmShiftEmployeeForm): sets confirmed=1 + confirm metadata; **creates the Timesheet** (also created on any save where confirmed=1 — one per assignment, unique `shift_employee_id`); stamps event PO/travel onto timesheet; records attestation answers; sends "shift confirmed" email/push if enabled; if confirm happens within deadline window, settings-controlled <24h notifications fire.
- **Admin decline** (DeclineShiftEmployeeForm): cancel_reason=12 (prohibitive — employee can't re-apply).
- **Employee declines an invite**: cancel_reason=11.
- **Employee cancels** (EmployeeCancelShiftForm / mobile): reason text from dictionary; system sets 2 vs 3 by 24-hour proximity; <24h is prohibitive and triggers staff notification (per settings).
- **Client cancels / decreases staff**: reasons 4/5; if inside the client's cancellation deadlines, the timesheet is set `*_worked=CANCELLED` with **min bill** (reason 4/5 → client billed) and **min pay** (employee paid) flags per the respective deadlines.
- **Retract** (employee withdraws request before confirm) and **Resend invite** actions exist.
- **Move** (MoveShiftEmployeeForm / payroll MoveShift): transfer an assignment to a different shift position/event; timesheet follows (event_id sync); reason 7 used when moving creates a replacement record.
- **No Call No Show**: reason 14 (prohibitive), timesheet NOSHOW.

On any save: the shift position recounts `filled`; insert sends invite/apply notification email (subject "GoLive! You Have Applied to Work at …" or "GoLive! You have been Requested to Work at …" with confirm link) honoring user settings; deleting an assignment deletes its timesheet.

### 10.5 Time-change re-request rule (T1323)

When shift times change: every non-cancelled assignee is reset to `confirmed=0` (re-request). If the change is within the bill/pay cancellation deadline (measured against the **old** start), the timesheet is first marked CANCELLED with min bill and min pay — the employee/client is compensated for the original commitment, and a fresh confirmation cycle begins.

### 10.6 Remove from future (DNR / termination support)

`removeFromFuture(employee, venue?)`: deletes the employee's PublishEmployee rows (optionally venue-scoped), and for every future non-cancelled assignment: if within deadline → timesheet CANCELLED + min pay + min bill and reason "Client Cancelled"; else reason Other; records remover; optional notification suppression.

---

## 11. Timesheets

### 11.1 The dual-sheet model

One Timesheet per confirmed assignment (`shift_employee_id` unique). It holds **two parallel sheets** — everything exists twice with `employee_` and `client_` prefixes:

Per sheet: `worked` status (WORKED / NOSHOW / SENTHOME / CANCELLED), `start`, `end`, first meal break `break_start/break_end` + `had_meal` + `no_break_reason`, second meal break `sec_break_start/sec_break_end` + `had_sec_meal` + `no_sec_break_reason`, `less_hours_reason` (1 = client sent home early, 2 = left shift early), `seconds` (computed net worked seconds), `notes`, `submit_date`, `rating` (client rates employee 1–5; employee rates client), money add-ons `tips`, `parking`, `travel`, `service_charge` (%), `no_break_penalty` (hours, 1 or 2), `adjustment` + `adjustment_notes`, and min-pay/min-bill flags: `employee_no_pay`, `employee_min_pay`, `client_no_bill`, `client_min_bill`.

Shared fields: `use_sheet` (NULL = reconcile both; CLIENT or EMPLOYEE = that sheet is authoritative), `status` (NEW / IN_PROGRESS / COMPLETED), `client_po`, `employee_travel_distance` (required when position has `miles_apply`), `reimbursement_file`, `employee_timesheet_upload`, verification fields `start_verified`/`end_verified` ∈ VERIFIED / AUTO_VERIFIED / MANUALLY_VERIFIED / WAS_VERIFIED / NON_VERIFIED with `start_verified_at`/`end_verified_at`.

### 11.2 Time entry rules

- All entries are **rounded to the whole minute** (≥30s rounds up) on save.
- Ordering validation per sheet: start required before breaks; break_end ≥ break_start; end ≥ all earlier entries; start ≠ end; entries cannot date before the event date. If start==end the whole sheet's times are cleared.
- `seconds` is computed (also by DB trigger) as: 0 if not worked; else (end−start) − positive break durations − positive second-break durations; negative → 0. "Filled" = has start+end, or is a non-worked status, or SENTHOME.
- A timesheet's hours can come from the **client sheet** (paper timesheet keyed by staff / client portal entry) and the **employee sheet** (mobile clock in/out or manual entry). When both are filled: no discrepancy → status COMPLETED; discrepancy → IN_PROGRESS pending resolution.
- **Discrepancy** = sheets disagree beyond tolerance (state/date-versioned calculators; effectively any material difference in times/worked status). Resolution: staff picks `use_sheet` or uses **copy from client / copy from employee** (copies all time fields, meal flags, reasons; penalties too when both calculations match), or adjusts manually.
- `getSeconds()` precedence: honor `use_sheet` if set; else require both filled and no discrepancy (then client sheet is used).

### 11.3 Worked statuses and the 50% / sent-home rules

- Marked WORKED but recorded hours ≤ 50% of scheduled (i.e., ≤ min-billing hours) **and** less_hours_reason = "client sent home early" → status auto-converted to **SENTHOME**.
- SENTHOME (employee sheet) → `employee_min_pay = 1` automatically.
- CANCELLED within deadline (client cancel, shift time change, removal) → min pay + min bill set (see §10).
- NOSHOW: either side marks no-show; flipping employee_worked→NOSHOW emails the venue's staffing manager ("missed event"), client_worked→NOSHOW likewise. A cron auto-NOSHOWs employees who never clocked in.
- Min pay / min bill flags are **only applied in CA, WA, NY** (`miniumPaymentStates`) — Nevada etc. get no minimum-pay floor.

### 11.4 Minimum billing hours

`minBillingHours = workHours(original scheduled times) / 2` (CA/NV/WA default); **New York: flat 3.0 hours**. `workHours` = shift length minus due meal-break durations per state schema. Old (pre-change) shift times are used if the shift was modified. When a min-pay/min-bill flag is set, payable/billable regular hours are recomputed: start from minBillingHours, subtract late arrival if under, use actual hours if more, then clamp to **[2, 4] hours** (the legacy clamp in ShiftCalculation).

### 11.5 Timeclock (employee mobile clock in/out)

Endpoints: start, stop, break-start, break-stop, adjust, verify, no-break-reason, less-hour-reason. Behavior by event `timeclock` mode:

- CLIENT: only client/staff fill times.
- EMPLOYEE / CLIENT_EMPLOYEE: employee clocks in/out in the app (GPS-based screens); client may also fill their sheet.
- EMPLOYEE_CODE: employee must enter the 4-digit clock-in/out code held by the code holder (event/shift/position/assignment level; positions can opt out via `disable_timeclock_code`). Code entry sets `start_verified_at`/`end_verified_at`. Verification: if claimed start precedes the verified scan by more than `timeclock_tolerance` minutes → WAS_VERIFIED (flagged), else VERIFIED. Clock-in allowed starting `timeclock_prestart_interval` minutes before shift start. `timeclock_limit` caps hours.
- KRONOS / ATLAS / NOWSTA: in-app editing disabled ("Please use the {system} system."); hours come from imports (payroll timesheet import).
- DISABLED: no employee timeclock.

Verification states feed “Timesheet Verification” reports and the payroll grid (auto/manual verify actions exist; `forVerification` calculations price unverified shifts by scheduled hours until resolved).

### 11.6 Edit permissions (canModify)

- Date range closed by **TimesheetClose** (payroll lock periods `from`–`to`): only ADMIN group with superAdmin or payroll role may edit; others see "contact Payroll".
- External timeclock event: nobody edits in-app.
- Client users: may edit only after shift end + 10 minutes ("You will be able to edit this timesheet after …").
- Employees: may edit until 1 hour after their `employee_submit_date`; afterwards locked ("contact Payroll").
- TimesheetOpen rows track staff "opening" an event's sheets; TimesheetSent tracks emailing timesheets/rosters to clients (re-editing a shift resets sent status); TimesheetUpload stores scanned paper timesheets per event.

### 11.7 Notes & ratings

Employee `notes` (visible to staff; adding notes emails staffing manager), client `notes`, adjustments with notes, employee star rating by client (1–5; payroll grid shows it), client rating by employee. PayrollNote (typed, per assignment, `on_sheet` flag) for payroll-facing annotations.

---

## 12. State Labor Rules: Meal Breaks, Penalties, Minimum Billing

Rules are resolved by **event state + event date** (versioned calculators; dates below are version cutovers in the legacy data — a rebuild needs only the latest):

### 12.1 Meal break schemas (threshold = shift hours that trigger a due break; duration in hours)

| State | Break 1 | Break 2 | Penalty 1 | Penalty 2 |
|---|---|---|---|---|
| California (& default) | >5h → 0.5h | >10h → 0.5h | >5h → 1.0h pay | >10h → 1.0h pay |
| Nevada | >8h → 0.5h | >11h → 0.5h | none | none |
| Washington | >5h → 0.5h | >11h → 0.5h | >5h → 0.5h | >11h → 0.5h |
| New York | >6h → 0.5h (0.75h if shift starts ≥13:00 or <06:00) | n/a | >6h → same as duration | n/a |

`workHours(shift)` = raw hours − sum of due break durations (used for scheduled-hour math and min billing).

### 12.2 Meal-break (no-break) penalty calculation

For the authoritative sheet: a break is "missing" when meal fields are filled but the employee had no meal, or the recorded break is **shorter than the required duration** — unless the no-break reason is a **waived** reason (current CA set: employee chose to waive / chose not to take). Client-side penalties are skipped entirely when the event's `no_break_penalty` is Disabled (client setting). The latest CA calculator only penalizes break 1. Penalty amount = penalty hours × rate (pay rate for employee, bill rate for client), split per break; **MSP override**: if client's MSP has `penalty_formula = PAY_RATE_BY`, the **bill** penalty = pay rate × MSP multiplier instead of bill rate. Stored on the timesheet as `*_no_break_penalty` hours (1 or 2) and priced in reports.

### 12.3 Minimum billing hours

California/Nevada/Washington: half the scheduled work hours; New York: 3.0 hours flat. Used for the 50% sent-home test and min pay/bill pricing (with the [2,4] clamp at pricing time).

---

## 13. Overtime Engine

Calculated per employee per week (Mon–Sun) across **all clients** (shifts grouped chronologically), from confirmed assignments + timesheet actuals (hybrid: actual hours when present, else scheduled). Results stored as JSON on each `shift_employee.overtime` = `{overtimes: {client: [...], employee: [...]}, paidBy, multipleStates, insufficientTimesheets}`.

State calculators (resolved by employee work state / event state; client `overtime_calculator` can override):

- **California**: Daily — first 8h regular; next up to 4h OT; beyond 12h DT. Weekly — hours beyond 40 (counting ≤8h/day) OT. 7th consecutive day — first 8h OT, beyond 8h DT.
- **Nevada**: Daily over 8h OT; weekly over 40 OT; no DT, no 7th-day rule.
- **New York / Washington**: weekly over 40 OT only.

**OT pay rate = blended weekly average** ((Σ hours×rate + worked add-ons: additional shift pays, bonuses, employee additional payments) / Σ hours) × 1.5 for OT, × 2.0 for DT. **Billing**: the same engine computes client-attributed overtime; `overtime_paid_by` decides if OT hours are billed to the client (using client OT/DT bill rates; the client's date-ranged `ClientMultiplier.ot/dt` overrides 1.5/2.0) or **absorbed by the agency** (client billed regular rate; employee still paid OT). If no client-attributable OT exists, paidBy defaults to AGENCY. Multi-state weeks and missing timesheets are flagged. Payroll has an **Overtimes tab / Action Center queue** where staff review each computed OT and choose/confirm who pays, with reason.

---

## 14. Pay & Bill Calculation (per shift)

For every assignment, `ShiftCalculation` (state-variant: California default, Nevada, NewYork) produces **payHours/billHours** (regular, overtime, doubletime, nonWorked, noBreakPenalty) and **payAmounts/billAmounts**:

1. **Base hours**: net timesheet hours per §11.2 precedence (client sheet for bill, employee sheet for pay when both used).
2. **Min pay/min bill**: if flagged, regular hours = minBillingHours adjusted for late arrival, clamped [2,4] (§11.4). SENTHOME/CANCELLED also produce `nonWorked` hours = max(regular − actual, 0), priced at the regular rate.
3. **Overtime split**: regular hours reduced by OT/DT hours (client side only when `overtime_paid_by=CLIENT`; employee side always). OT/DT hours priced at the blended rates from §13.
4. **Amounts**: regular = hours × rate (pay rate / bill rate from the assignment); holiday flag exposes `holidayRate` (legacy UI doubles when checked); `bonus` and **AdditionalShiftPay** sum added to pay when both sides show worked; tips (`employee_tips`/`client_tips`, exports can apply a tips multiplier); parking; travel (flat from event/venue); travel-by-distance = `employee_travel_distance` × TravelRate (pay & bill rates for the event date); service charge = rate × % (+ flat venue `service_charge` on bill); meal penalties per §12.2.
5. Client invoice totals additionally apply MSP markup %, client discount, late fees per attached policies, and the holiday multiplier where flagged.

(For verification-mode pricing, scheduled hours are used until timesheets are filled.)

---

## 15. Payroll Module, Reports & Invoicing

### 15.1 Payroll workspace (admin)

Three tabs: **Shifts** (the payroll grid: every assignment in a date range with employee/client hours, statuses, verification, rates, computed amounts, discrepancy badges, notes; filters by client/venue/employee/date/status; per-row actions: open timesheet drawer with both sheets, copy-from-client/employee, set use_sheet, mark worked/sent-home/no-show/cancelled with min-pay/min-bill prompts, set overtime payer, move shift, email employee, email manager, timesheet history (audit trail), batch operations), **Other Works** (EmployeeOtherWork rows for the period), **Overtimes** (review/choose OT payer). Plus: hours-reminder email blast to clients (only those with `payroll_hours_reminder_enabled`), timesheet export/import (CSV round-trip, incl. external-timeclock events), send timesheets/rosters to client contacts flagged `timesheet`, TimesheetClose management (lock periods).

### 15.2 Payroll exports (the report engine — these are the payroll-system/invoice feeds)

Generated sync or as queued background **Jobs** (job list UI with statuses, results downloadable; files also emailable). Full catalog:

| Report | Content |
|---|---|
| Clients | Per-client billing detail for range |
| Employee | Per-employee pay detail |
| All Clients and Employees (+ "new" variant) | Master pay+bill workbook: hours by type (reg/OT/DT/penalty), amounts, add-ons, per shift — **the primary payroll upload feed** (tips multiplier option, MSP filter) |
| Summary Report (+new, offset, offset-scheduled) | Per-client/venue totals; "offset" variants shift dates by client `invoices_offset`; "scheduled" prices unfilled timesheets at scheduled hours |
| Invoices | Invoice generator (§15.4) |
| Factored / Non-Factored Invoices | Same, split by venue `factored` flag |
| Timesheet Verification (+Clients) | Verification-state listing |
| Discrepancy | All timesheets with sheet mismatches/missing sides (hours both sides, who's missing, submit times, staffing manager, notes) |
| Gold Report Master / Individual Gold Sheet | Per-venue event completion checklists |
| Paycheck Mailing List | Employees on paper-check list who worked |
| Double Shift / 7 Days Worked / Over 40 Hours (+schedule-hours) / Overtimes | OT audit reports |
| Sick Pay / Wage Replacement / Benefit-Sick | SickWagePay listings (hours × rate, WC code) |
| Shift No Show / Cancelled <24 / No-Call-No-Show / Late Arrivals / Employee Shift Cancellations | Conduct reports |
| 1st Shift Employees / New Hires / Employee Tenure & Shift Count / Employee List / Human Resources | HR feeds (hire/rehire, first date worked, referred/recruited by, county, work state) |
| Open Shifts / New Positions / Candidate Partners / Certifications / Client & Venue Documents / Employee Documents / MSP Marriott / Client Billing / Timesheet Employee- & Client-Submitted Hours | Misc operational exports |

On-screen reports (admin → Reports): Conflict report (double-booking), Scheduled Over-40-hours, Scheduled 7-days, Timesheet discrepancies, DNR list, All pay rates, UCLA (bundle-specific), Venue contact list, Active county of residence, Current week shifts.

### 15.3 Payroll hours definition

"Payroll hours" for an assignment = reconciled seconds (§11.2) with min-pay floors applied; weekly OT splits per §13. The **employee payroll upload file** keys on `employee.payroll_id`.

### 15.4 Invoice generation

For a date range (+ optional invoice starting number): clients are processed honoring `invoices_offset` (each client's range shifted by its offset). Rows are grouped into invoice numbers by `separate_venue`:

- No → one invoice per client (uses client `invoice_name`).
- Invoice venues separately / venues as customers → one per venue (venue `invoice_name`).
- "…and Day" variants → one per venue **per event date**.
- By PO Number → one per PO value.

Sequential invoice numbers assigned from the starting number. Line items carry event date/title + `invoice_label`, position, hours by type, rates, add-ons (travel, parking, service, penalties, tips), MSP markup, discounts; net terms from client. Output = spreadsheet for the accounting system (plus Factored/Non-Factored variants keyed on venue `factored`).

---

## 16. Notifications, Email, SMS, Push

- **Notification** records (in-app message center): `title`, `body`, `long_body`, `type` (MESSAGE, SHIFT_REQUEST, SHIFT_CONFIRM, SHIFT_DECLINE, SHIFT_PUBLISH, SHIFT_CANCEL, SHIFT_UPDATE, EVENT_UPDATE, CERTIFICATION, SHIFT_TIMECLOCK, SHIFT_REMINDER, MISC), `severity` (SUCCESS/DANGER/WARNING/INFO), `data` (deep-link payload: target screen e.g. employee event view, timesheet form, client event view), attachments, `published_at`, `global` vs addressed (`notification_addressee`), `popup`, `email` mirror, `push_notification`, archived; per-user seen tracking (`notification_seen`, `notification_seen_until`). Admins can compose blasts to employees (with filters) — "Message Employees".
- **Push**: Expo (mobile) + Firebase (web) device registrations (`notification_subscription`: token, uuid, device name/platform, status). Push for publishes, confirmations, cancellations, reminders, timeclock nudges.
- **Email**: SMTP via queued mail jobs; templated (shift invite/confirm/change/removed templates editable); From defaults to staffing manager where relevant; BCC to system notification address. System notification routing configurable per type (SystemNotificationAddress) + legacy Preference emails.
- **SMS**: Nexmo/Vonage integration (`sms` log: number, message, status, inbound replies). Used for select alerts.
- **Tutorials** and **CompanyInfo** provide in-app help content per interface.
- **Touch** rows record "user X viewed object Y at time Z" (e.g., admin saw a new request).

## 17. Scheduled Automation (Cron Jobs)

Every minute: mail queue; publishing processor (≤4 parallel); general queue worker; 2nd & 3rd shift reminders; notification dispatcher. Daily (11am PT): employee inactivity warning/inactivation; 1st shift reminder; DNR notifications digest; employee background reminder; certification expiration notifications; daily welcome email for activated employees; archive old notifications; client MSA-expiry notification. Also available as commands: auto-NOSHOW for never-clocked-in employees, clock-in reminder (~shift start), background reset, requested-background notification.

Shift reminders: 1st = day-before digest; 2nd/3rd = near shift start (per-user toggles; third reminder escalates). Clock-in reminder pushes at shift start when not clocked in.

---

## 18. Admin Web Application — Screen Inventory

Routes (React SPA; rebuild as any UI):

- **Events**: calendar home (month/week views with per-event stat badges: positions/filled/confirmed/waiting); event list; event editor (form), event view (shifts, positions, assignees, publishings dialog, per-publishing detail), event timesheets (payroll form scoped to event), event change history; shift-position detail view; create-from-venue route.
- **Clients**: list; per-client menu → profile form, venues, positions (+rate history/batch changes), contacts, DNRs, timesheets, purchase orders, users (portal accounts + invitations), preferred list, notes, multipliers, documents, events calendar, certifications, attestation questions, change history.
- **Venues** (under client): form, contacts, positions, DNRs, preferred, events calendar, timesheets, attestation questions, documents, certifications, change history.
- **Employees**: list (rich filters); per-employee menu → profile form, DNRs, preferred, certifications, files, positions, notes, other-work, timesheets, schedule calendar, change history.
- **Payroll**: shifts / other-works / overtimes tabs (§15.1).
- **Reports**: conflict, scheduled-over-40, scheduled-7-days, timesheet discrepancies (+ the export catalog §15.2 via Payroll export dialog), System Queue (background job monitor).
- **Action Center**: profile-picture approval queue; certification approval queue; overtime decision queue (pending/chosen).
- **Setup**: users (admin staff accounts + settings + history), positions, uniforms, tools, grooming tools, min-wage rates, general company info, languages, status reasons, parkings, late-fee policies, WC codes, other work types, default employee positions, divisions, net terms, MSPs, billing types, document types, additional shift pays, travel rates, attestation questions, certifications, notification addresses, tutorials.
- **Misc**: notifications center, settings, profile, tutorials, invitation accept/decline/registration, password recovery/change, unsubscribe.

## 19. Client Portal — Screen Inventory

- Company selector (multi-client users); calendar home (own events; can create events where permitted).
- Event view: shifts & positions with staffing counts (if `staff_count_visible`), request-employee dialog (pick from eligible employees), confirm/decline employee dialogs, shift dialog (edit within `clientModifyShiftWhen` window), publishings list/detail, event edit.
- Hours (timesheet list & entry — client sheet only; locked until shift end +10 min; PDF export).
- Venues: list, venue form, contacts, positions (+rates view/edit where permitted), DNRs, preferred, documents.
- Company profile: form, venues, contacts, DNRs, preferred, documents, users (invite/manage portal users with role tiers).
- Profile/settings, notifications, tutorials, invitations, unsubscribe. Mobile app mirrors all of this (client tabs: Home calendar, Venues, Hours) plus overtime approval form.

## 20. Employee Web & Mobile App — Screen Inventory

Mobile (Expo; primary employee surface) tabs: **Home** (calendar of my shifts + key alerts), **Events** (published/available shift list with filters; event/shift detail with rate where visible, directions, parking, uniforms/tools/grooming, certifications needed, attestation; Apply / Cancel flows; "missing requirements" screen), **Hours** (timesheet list; per-shift timesheet form: clock in/out, breaks (1st/2nd) with had-meal/no-break reasons, less-hours reason, tips/travel-distance entry where applicable, notes, rating, billing summary, submit; versioned per state), **Company Info**. Drawer/other: profile (personal info, photo upload → approval queue, positions (request additions), work-area form with map radius, certifications upload/renewal, settings/notification toggles), health screenings, change history (admin-style on mobile for admins), notifications, tutorials, action center (admin variant), event map, sign-up & confirm, password recovery, unsubscribe. Push deep-links open the relevant screen.

Employee web mirrors: calendar, events list + detail, hours (old + new UIs), profile (+certifications, positions, work area, settings), company info, notifications, tutorials.

## 21. Registration & Invitation Flows

- **Employee self-registration** (public + mobile): basic identity + contact + RegistrationPosition choices + referred-by lookup; creates Candidate employee + user; email verification (`verify_token`/Activation records); blocked-email list enforced; INCREMENT_BY=2100 offsets public employee ids. Admin completes vetting → Active. *(Your new HR module replaces/extends this with full applications.)*
- **Client registration**: admin-initiated; ClientRegistrationForm creates client + first portal user.
- **Invitations**: staff invite client contacts (or additional users) by email with code; accept / decline / register-with-code routes set up the user + party + role.
- **Welcome flow**: daily welcome email to newly activated employees; onboarding forms (legacy `onboard_forms` captured I-9, W-4, 8850, EDD, EEO-1, background disclosure, signature data — superseded by Clickboarding integration, and by your future HR module).

## 22. External Integrations

| Integration | Purpose |
|---|---|
| Google Maps Geocoding | Address → lat/long for clients, venues, events, employees, work areas (distance matching) |
| Expo push / Firebase | Mobile & web push notifications |
| Nexmo (Vonage) | SMS send/receive log |
| AWS S3 (+local fallback) | File storage: employee files, certs, event docs, venue docs, timesheet scans, payroll exports, uniform images, temp files |
| Kronos / Atlas / Nowsta | External timeclocks: events flagged with these lock in-app timesheets; hours arrive via payroll import; per-employee MSP clock codes support reconciliation |
| Clickboarding | Employee onboarding/eVerify paperwork (replaces legacy onboard forms) |
| Dash | Employee data sync job |
| Salesforce | Passive id fields (`sf_id`) on client/contact/venue/user for CRM correlation |
| OAuth2 (password grant) | API auth for all three frontends |

## 23. Change History / Audit System

**HistoryEntry**: every significant mutation (events, shifts, shift positions, assignments, timesheets, publishings, profiles) writes `{related (root object), related_id, model, model_id, changes (i18n-able description + key/value diffs), keys (machine-readable tags e.g. status_to_COMPLETED), created_at/by}`. Specialized formatters render human text per model (event duplicated, shift time changed, employee auto-confirmed, timesheet status…). Admin UIs expose per-entity History tabs; **HistoryEntryNotification** lets staff email a digest of selected changes to interested parties (e.g., notify client of event changes). EmployeeProfileUpdates + Touch complement it. The auto-confirm "based on work" rule even queries history (`status_to_COMPLETED` timestamps) — the rebuild needs an equivalent queryable audit trail.

## 24. Rebuild Guidance & HR Module Integration Points

- **Keep**: the three-layer position/rate model with date-ranged amounts; dual-sheet timesheet with discrepancy reconciliation; cancellation-deadline min pay/bill; state-pluggable labor rules (meal break schema, penalty, min billing, OT calculator — design as strategy objects keyed by state, versioned by effective date); publishing eligibility pipeline; prohibitive cancel reasons; audit trail.
- **Payroll scope change**: you only need §15.2's export pipeline (especially "All Clients and Employees", Summary, Invoices, Discrepancy, Verification) emitting CSV/XLSX keyed on `payroll_id`; no in-app payment processing exists anyway.
- **HR module hooks**: Employee.hr_status + the legacy application fields (§7.1) and onboarding forms (§21) define the data the business expects from hiring; your in-app application flow should ultimately create the same Employee record (Candidate → vetting → Active), feed EmployeeGovernment/Documents, certifications, and DefaultEmployeePositions.
- **Simplifications worth considering**: collapse legacy versioned calculators to current versions; replace MySQL stored functions/triggers (`TIMESHEET_CALCULATE_SECONDS`, `EVENT_IN_DEADLINE`, payroll triggers) with application-layer code; unify the legacy duplicated notification-email config (Preference vs SystemNotificationAddress); drop Salesforce/Dash unless needed.
- **Names that must not leak into the new product**: "GoLive", "Culinary Staffing" appear in email subjects/templates throughout — make all copy configurable.

## 25. Appendix A — Full Data Dictionary

The complete table/column inventory extracted from the legacy models (types simplified). Junction tables of shape `(id, left_id, right_id, created_at, created_by)` are listed once by name. See sections above for semantics.

**Core**: client, client_contact, client_note, client_document, client_holiday, client_late_fee, client_multiplier, client_purchase_order, client_background, client_certification, client_position, client_position_amount, client_position_uniform/tool/grooming_tool/certification, venue, venue_contact, venue_document, venue_language, venue_picture, venue_certification, venue_attestation_question, venue_position, venue_position_amount, venue_position_uniform/tool/grooming_tool/certification, employee, employee_position, employee_certification, employee_uniform/tool/grooming_tool, employee_background, employee_language, employee_document, employee_government, employee_note, employee_other_work, employee_clock_code, employee_available, employee_profile_updates, employee_health_screening, hiatus_employee, sick_wage_pay, sick_wage_employee, event, event_document, event_edits, event_cancellation, shift, shift_position, shift_position_uniform/tool/grooming_tool/certification, shift_employee, shift_employee_document, shift_employee_certification, timesheet, timesheet_close, timesheet_open, timesheet_sent, timesheet_upload, publishing, publishing_shift, publishing_language, publish_employee, publish (legacy), dnr, exclusive.

**Reference**: position, position_uniform/tool/grooming_tool/certification, position_material, position_sub_type, uniform, tool, grooming_tool, certification, language, min_wage_rate, min_wage_rate_amount, wc_code, late_fee_policy, status_reason, division, msp, net_terms_entry, billing_type, document_type, attestation_question, additional_shift_pay, travel_rate, holiday, parking, other_work_type, registration_position(_position), default_employee_position, county, state, background, health_screening, client_health_screening.

**Platform**: user, user_party, user_device, auth_item/auth_item_child/auth_rule/auth_assignment, activation, blocked_email, notification, notification_addressee, notification_seen(_until), notification_subscription, push_notifications, sms, history_entry, history_entry_notification, touch, tutorial, tutorial_seen, company_info(_item), templates, preference, system_notification_address, job, temporary_file, payroll_note(_type), onboard_forms, government_form, staff (legacy), task (legacy), calendar.

Field-level details for every table are in the legacy model docblocks (`culinary-be/common/models/base/*.php`) and were the source for this specification; the business-critical fields are all described in §§3–15 above.

---

*End of specification. Generated 2026-06-10 by reverse-engineering the `culinary-be`, `culinary-fe`, and `culinary-mobile` codebases.*
