"""
Simple JSON-file based storage for HR data: employees, leave requests, and
company policies. Good enough for a small team getting started; for a
growing company, swap this for a real database (see README "Next steps").

IMPORTANT: on most cloud hosts (Railway, Render free tiers) this file lives
on the container's disk, which can be wiped on redeploy. Your data is safe
between normal restarts, but back it up (download data/hr_data.json
occasionally) if it matters to you.
"""

import json
import os
import threading

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HR_DATA_FILE = os.path.join(DATA_DIR, "hr_data.json")
POLICIES_FILE = os.path.join(DATA_DIR, "policies.json")

_lock = threading.Lock()


def _load(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_hr_data() -> dict:
    return _load(HR_DATA_FILE, {"employees": [], "leave_requests": []})


def load_policies() -> dict:
    return _load(POLICIES_FILE, {})


# ---- Employees -----------------------------------------------------------

def add_employee(name: str, role: str = "", notes: str = "") -> dict:
    with _lock:
        data = _load_hr_data()
        employee = {
            "id": len(data["employees"]) + 1,
            "name": name,
            "role": role,
            "notes": notes,
        }
        data["employees"].append(employee)
        _save(HR_DATA_FILE, data)
        return employee


def find_employees(query: str) -> list:
    data = _load_hr_data()
    query = (query or "").strip().lower()
    if not query:
        return data["employees"]
    return [
        e for e in data["employees"]
        if query in e["name"].lower() or query in e.get("role", "").lower()
    ]


def update_employee(employee_id: int, name: str = None, role: str = None, notes: str = None):
    with _lock:
        data = _load_hr_data()
        for e in data["employees"]:
            if e["id"] == employee_id:
                if name is not None:
                    e["name"] = name
                if role is not None:
                    e["role"] = role
                if notes is not None:
                    e["notes"] = notes
                _save(HR_DATA_FILE, data)
                return e
        return None


# ---- Leave requests --------------------------------------------------------

def request_leave(employee_name: str, start_date: str, end_date: str, reason: str = "") -> dict:
    with _lock:
        data = _load_hr_data()
        leave = {
            "id": len(data["leave_requests"]) + 1,
            "employee_name": employee_name,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "status": "pending",
        }
        data["leave_requests"].append(leave)
        _save(HR_DATA_FILE, data)
        return leave


def list_leave_requests(employee_name: str = "", status: str = "") -> list:
    data = _load_hr_data()
    results = data["leave_requests"]
    if employee_name:
        results = [r for r in results if employee_name.lower() in r["employee_name"].lower()]
    if status:
        results = [r for r in results if r["status"] == status]
    return results


def update_leave_status(leave_id: int, status: str):
    with _lock:
        data = _load_hr_data()
        for r in data["leave_requests"]:
            if r["id"] == leave_id:
                r["status"] = status
                _save(HR_DATA_FILE, data)
                return r
        return None
