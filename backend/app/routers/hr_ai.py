"""HR AI endpoints — CV parsing, payroll anomaly detection, attrition risk,
UAE compliance checks, leave pattern analysis, JD generation, HR chatbot.
All endpoints pull real data from the database before calling OpenAI."""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.limiter import limiter
from app.models import AppDataRecord, Employee, PayrollItem, PayrollRun, User

router = APIRouter(prefix="/ai/hr", tags=["hr ai"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _openai_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def _anthropic_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _call_ai(prompt: str, system: str = "You are an expert HR consultant for UAE companies. Always respond with valid JSON only — no markdown, no explanation outside the JSON.") -> dict[str, Any]:
    """Call OpenAI (preferred) or Anthropic. Returns parsed dict."""
    openai_key = _openai_key()
    anthropic_key = _anthropic_key()

    if openai_key:
        payload = {
            "model": os.environ.get("OPENAI_HR_MODEL", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 2500,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return {"error": f"OpenAI request failed ({exc.code}): {exc.reason}"}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"error": f"OpenAI request failed: {exc}"}
        raw = result["choices"][0]["message"]["content"].strip()
    elif anthropic_key:
        payload = {
            "model": os.environ.get("ANTHROPIC_HR_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 2500,
            "messages": [{"role": "user", "content": f"{system}\n\n{prompt}"}],
        }
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode(),
            headers={
                "x-api-key": anthropic_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                result = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return {"error": f"Anthropic request failed ({exc.code}): {exc.reason}"}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return {"error": f"Anthropic request failed: {exc}"}
        raw = result["content"][0]["text"].strip()
    else:
        return {"error": "No AI API key configured. Add OPENAI_API_KEY or ANTHROPIC_API_KEY to .env"}

    # strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _employee_rows(db: Session, company_id: str) -> list[dict[str, Any]]:
    """Return all employees from the Employee table."""
    rows = db.query(Employee).filter(Employee.company_id == company_id).all()
    return [
        {
            "id": e.id,
            "employee_no": e.employee_no,
            "name": e.full_name,
            "department": e.department,
            "designation": e.designation,
            "basic_salary": float(e.basic_salary or 0),
            "has_iban": bool(e.iban),
            "has_wps": bool(e.wps_id),
            "status": e.status,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in rows
    ]


def _payroll_history(db: Session, company_id: str) -> list[dict[str, Any]]:
    """Return payroll items joined with employee and run data."""
    items = (
        db.query(PayrollItem, Employee, PayrollRun)
        .join(Employee, PayrollItem.employee_id == Employee.id)
        .join(PayrollRun, PayrollItem.run_id == PayrollRun.id)
        .filter(PayrollRun.company_id == company_id)
        .order_by(PayrollRun.period.desc())
        .all()
    )
    return [
        {
            "employee_no": e.employee_no,
            "name": e.full_name,
            "department": e.department,
            "period": pr.period,
            "basic": float(pi.basic or 0),
            "allowances": float(pi.allowances or 0),
            "overtime": float(pi.overtime or 0),
            "deductions": float(pi.deductions or 0),
            "net_pay": float(pi.net_pay or 0),
            "run_status": pr.status,
        }
        for pi, e, pr in items
    ]


def _app_data_records(db: Session, company_id: str, collection: str, limit: int = 200) -> list[dict[str, Any]]:
    """Pull AppDataRecord rows for a given collection and parse payloads."""
    rows = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection)
        .order_by(AppDataRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in rows:
        try:
            out.append(json.loads(r.payload or "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
    return out


# ── request / response models ────────────────────────────────────────────────

class CvParseRequest(BaseModel):
    text: str


class JdGenerateRequest(BaseModel):
    title: str
    department: str
    requirements: str = ""
    salary_range: str = ""
    experience_years: str = ""


class ChatbotRequest(BaseModel):
    question: str


class LeaveAnalysisRequest(BaseModel):
    leave_data: list[dict[str, Any]] = []


# ── endpoints ────────────────────────────────────────────────────────────────

@router.post("/cv-parse")
@limiter.limit("15/minute")
def cv_parse(request: Request, payload: CvParseRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Extract structured employee fields from raw CV / resume text."""
    prompt = f"""Extract HR employee data from this CV/resume text and return JSON.

CV TEXT:
{payload.text[:4000]}

Return exactly this JSON structure:
{{
  "full_name": "",
  "email": "",
  "phone": "",
  "nationality": "",
  "visa_type": "",
  "department_suggestion": "",
  "designation": "",
  "basic_salary_suggestion": 0,
  "years_experience": 0,
  "skills": [],
  "education": [],
  "previous_employers": [],
  "languages": [],
  "uae_driving_license": false,
  "notes": ""
}}"""
    return _call_ai(prompt)


@router.post("/payroll-anomaly")
@limiter.limit("15/minute")
def payroll_anomaly(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Detect payroll anomalies using real payroll data from the database."""
    employees = _employee_rows(db, current_user.company_id)
    payroll = _payroll_history(db, current_user.company_id)

    if not employees:
        return {"anomalies": [], "summary": "No employee records found in database.", "total_checked": 0}

    prompt = f"""You are a payroll auditor for a UAE company. Analyze this payroll data and detect anomalies.

EMPLOYEE MASTER DATA ({len(employees)} employees):
{json.dumps(employees, indent=2)}

PAYROLL HISTORY ({len(payroll)} records):
{json.dumps(payroll, indent=2)}

Look for these anomalies:
- Net pay higher or lower than expected based on basic salary
- Deductions that seem too high or zero when they shouldn't be
- Employees paid in a run but not in previous runs (new addition check)
- Salary inconsistencies between master data and payroll items
- Missing IBAN or WPS ID for active employees
- Any employee with overtime in department that normally doesn't have it

Return JSON:
{{
  "anomalies": [
    {{
      "employee_no": "",
      "employee_name": "",
      "type": "Salary Mismatch|Missing IBAN|Deduction Anomaly|OT Flag|New Employee|Other",
      "severity": "High|Medium|Low",
      "description": "",
      "expected": "",
      "actual": "",
      "action": ""
    }}
  ],
  "summary": "one paragraph summary",
  "total_checked": 0,
  "clean_count": 0,
  "anomaly_count": 0
}}"""

    result = _call_ai(prompt)
    result["db_employees"] = len(employees)
    result["db_payroll_records"] = len(payroll)
    return result


@router.post("/attrition-risk")
@limiter.limit("15/minute")
def attrition_risk(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Score each employee's attrition risk based on DB data."""
    employees = _employee_rows(db, current_user.company_id)
    payroll = _payroll_history(db, current_user.company_id)

    if not employees:
        return {"scores": [], "summary": "No employee records found.", "high_risk_count": 0}

    # Build payroll summary per employee
    payroll_by_emp: dict[str, list] = {}
    for p in payroll:
        payroll_by_emp.setdefault(p["employee_no"], []).append(p)

    enriched = []
    for emp in employees:
        emp_payroll = payroll_by_emp.get(emp["employee_no"], [])
        avg_net = sum(p["net_pay"] for p in emp_payroll) / max(1, len(emp_payroll))
        enriched.append({**emp, "payroll_runs": len(emp_payroll), "avg_net_pay": round(avg_net, 2)})

    prompt = f"""You are an HR analytics expert. Score each employee's attrition/resignation risk for a UAE company.

EMPLOYEE DATA ({len(enriched)} employees):
{json.dumps(enriched, indent=2)}

Risk factors to consider (UAE context):
- No IBAN/WPS (indicates possible undocumented issues)
- Low salary relative to department and designation (compare peers in same dept)
- Short tenure (created_at recently) — new joiners have higher turnover
- Departments with historically higher turnover: Sales, Procurement
- No payroll runs (not yet paid — risky)

Return JSON:
{{
  "scores": [
    {{
      "employee_no": "",
      "name": "",
      "department": "",
      "designation": "",
      "risk_score": 0,
      "risk_level": "Low|Medium|High|Critical",
      "risk_factors": [],
      "retention_actions": []
    }}
  ],
  "summary": "one paragraph executive summary",
  "high_risk_count": 0,
  "medium_risk_count": 0,
  "low_risk_count": 0,
  "top_retention_priorities": []
}}"""

    return _call_ai(prompt)


@router.post("/compliance-check")
@limiter.limit("15/minute")
def compliance_check(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Check UAE Labor Law compliance for all employees in the database."""
    employees = _employee_rows(db, current_user.company_id)
    payroll = _payroll_history(db, current_user.company_id)

    if not employees:
        return {"issues": [], "summary": "No employees to check.", "compliant_count": 0}

    paid_emp_nos = {p["employee_no"] for p in payroll}

    prompt = f"""You are a UAE Labor Law compliance expert. Check compliance for each employee.

UAE FEDERAL DECREE-LAW No. 33 of 2021 checks:
- All employees must have IBAN for WPS (Wages Protection System)
- WPS ID must be present for payroll processing
- Employees in active status must be in payroll runs
- Gratuity applies after 1 year of service
- No employee should have net pay less than basic salary without valid deductions

EMPLOYEE DATA ({len(employees)} employees):
{json.dumps(employees, indent=2)}

EMPLOYEES IN PAYROLL: {list(paid_emp_nos)}

Return JSON:
{{
  "issues": [
    {{
      "employee_no": "",
      "name": "",
      "department": "",
      "issue_type": "Missing IBAN|Missing WPS ID|Not in Payroll|Salary Below Minimum|Data Incomplete|Other",
      "severity": "Critical|High|Medium|Low",
      "law_reference": "UAE Labor Law Article ...",
      "description": "",
      "recommended_action": "",
      "deadline": ""
    }}
  ],
  "summary": "compliance summary paragraph",
  "compliant_count": 0,
  "non_compliant_count": 0,
  "critical_count": 0,
  "overall_compliance_score": 0
}}"""

    return _call_ai(prompt)


@router.post("/leave-analysis")
@limiter.limit("15/minute")
def leave_analysis(
    request: Request,
    payload: LeaveAnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Analyze leave patterns. Accepts leave data from frontend (AppDataRecord via JS)."""
    # Also try to pull from DB
    db_leave = _app_data_records(db, current_user.company_id, "hrLeaveRequests", 500)
    db_leave += _app_data_records(db, current_user.company_id, "leaveRequests", 500)

    all_leave = db_leave + payload.leave_data
    employees = _employee_rows(db, current_user.company_id)

    if not all_leave:
        return {
            "patterns": [],
            "summary": "No leave data found. Leave requests will be analyzed as employees submit them.",
            "total_records": 0,
            "insights": ["No leave records in database yet — add leave requests to see AI analysis."]
        }

    prompt = f"""You are an HR analytics expert. Analyze leave patterns for a UAE company.

EMPLOYEE LIST ({len(employees)} total):
{json.dumps([{"no": e["employee_no"], "name": e["name"], "dept": e["department"]} for e in employees], indent=2)}

LEAVE RECORDS ({len(all_leave)} records):
{json.dumps(all_leave[:100], indent=2)}

Analyze for:
- Employees consistently taking leave on specific days (Monday/Friday patterns)
- Departments with high leave concentration
- Low leave utilization (burnout risk — employees not taking entitled leave)
- Leave spikes around public holidays
- Employees who haven't taken leave in 6+ months
- Leave balance trends

Return JSON:
{{
  "patterns": [
    {{
      "pattern_type": "Day Pattern|Burnout Risk|Holiday Spike|Department Cluster|Unused Leave|Other",
      "severity": "Info|Watch|Alert",
      "description": "",
      "affected_employees": [],
      "recommendation": ""
    }}
  ],
  "summary": "executive summary",
  "total_records": 0,
  "insights": [],
  "department_breakdown": {{}},
  "burnout_risk_employees": []
}}"""

    return _call_ai(prompt)


@router.post("/jd-generate")
@limiter.limit("15/minute")
def jd_generate(request: Request, payload: JdGenerateRequest, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Generate a UAE-compliant Job Description using AI."""
    salary_hint = f"Salary range: {payload.salary_range} AED/month" if payload.salary_range else ""
    exp_hint = f"Experience required: {payload.experience_years} years" if payload.experience_years else ""

    prompt = f"""Generate a professional Job Description for a UAE company.

Position: {payload.title}
Department: {payload.department}
{salary_hint}
{exp_hint}
Additional requirements: {payload.requirements or "Standard UAE office role"}

Return JSON:
{{
  "job_title": "",
  "department": "",
  "reports_to": "",
  "location": "Dubai, UAE",
  "employment_type": "Full-Time",
  "job_summary": "",
  "key_responsibilities": [],
  "required_qualifications": [],
  "preferred_qualifications": [],
  "technical_skills": [],
  "soft_skills": [],
  "salary_range": "",
  "benefits": ["Health Insurance", "Annual Leave per UAE Labor Law", "End-of-Service Gratuity"],
  "uae_requirements": ["Valid UAE residence visa or eligibility", "UAE driving license (if applicable)"],
  "about_company": "An established company in the UAE committed to excellence and growth.",
  "application_instructions": "Apply with updated CV and cover letter.",
  "keywords": []
}}"""

    return _call_ai(prompt)


@router.post("/chatbot")
@limiter.limit("15/minute")
def chatbot(
    request: Request,
    payload: ChatbotRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """HR chatbot with real company data context from the database."""
    employees = _employee_rows(db, current_user.company_id)
    payroll = _payroll_history(db, current_user.company_id)

    # Build compact context
    dept_counts: dict[str, int] = {}
    total_salary = 0.0
    for e in employees:
        dept_counts[e["department"]] = dept_counts.get(e["department"], 0) + 1
        total_salary += e["basic_salary"]

    runs = list({p["period"] for p in payroll})
    runs.sort(reverse=True)

    context_summary = {
        "total_employees": len(employees),
        "departments": dept_counts,
        "total_monthly_salary_budget": round(total_salary, 2),
        "recent_payroll_periods": runs[:3],
        "employees_without_iban": sum(1 for e in employees if not e["has_iban"]),
        "employees_without_wps": sum(1 for e in employees if not e["has_wps"]),
    }

    prompt = f"""You are an intelligent HR assistant for a UAE company. Answer the employee/HR question using the company data below.

COMPANY HR DATA SUMMARY:
{json.dumps(context_summary, indent=2)}

EMPLOYEE LIST:
{json.dumps([{"no": e["employee_no"], "name": e["name"], "dept": e["department"], "designation": e["designation"], "salary": e["basic_salary"]} for e in employees], indent=2)}

USER QUESTION: {payload.question}

Answer helpfully and accurately. If the question is about specific numbers, use the data above.
Return JSON:
{{
  "answer": "detailed helpful answer",
  "data_used": ["what data you referenced"],
  "follow_up_questions": ["2-3 related questions the user might ask"],
  "confidence": 0
}}"""

    result = _call_ai(prompt)
    result["context_used"] = context_summary
    return result
