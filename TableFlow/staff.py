"""Staff attendance and monthly payroll for manager routes."""

import calendar
import csv
import io
import re
from collections import Counter
from datetime import date, datetime
from html import escape

from operations import OperationError, TIME_ZONE, money

STATUSES = ("Present", "Half day", "Absent", "Paid leave", "Weekly off")
SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT,
    monthly_salary INTEGER NOT NULL CHECK(monthly_salary > 0)
);
CREATE TABLE IF NOT EXISTS attendance (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    day TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(employee_id, day)
);
CREATE TABLE IF NOT EXISTS payroll (
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    month TEXT NOT NULL,
    bonus INTEGER NOT NULL DEFAULT 0,
    other_deduction INTEGER NOT NULL DEFAULT 0,
    monthly_salary INTEGER,
    paid_amount INTEGER,
    paid_on TEXT,
    PRIMARY KEY(employee_id, month)
);
"""


def today():
    return datetime.now(TIME_ZONE).date()


def parse_day(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise OperationError("Choose a valid date.")


def month_dates(month):
    start = parse_day(month + "-01")
    days = calendar.monthrange(start.year, start.month)[1]
    return start, start.replace(day=days), days


def amount(value, optional=False):
    if not value and optional:
        return 0
    if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", value):
        raise OperationError("Enter a valid amount in rupees with up to two decimal places.")
    rupees, _, fraction = value.partition(".")
    return int(rupees) * 100 + int(fraction.ljust(2, "0"))


def rounded(numerator, denominator):
    return (2 * numerator + denominator) // (2 * denominator)


def csv_text(value):
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def payroll_rows(db, month, as_of):
    start, end, days = month_dates(month)
    employees = db.execute("""
        SELECT e.*, COALESCE(p.monthly_salary,e.monthly_salary) AS rate,
               COALESCE(p.bonus,0) AS bonus, COALESCE(p.other_deduction,0) AS other_deduction,
               p.paid_amount, p.paid_on
        FROM employees e LEFT JOIN payroll p ON p.employee_id=e.id AND p.month=?
        WHERE e.starts_on<=? AND (e.ends_on IS NULL OR e.ends_on>=?)
        ORDER BY e.name COLLATE NOCASE,e.id
    """, (month, end.isoformat(), start.isoformat())).fetchall()
    attendance = {}
    for mark in db.execute("""
        SELECT a.employee_id,a.status FROM attendance a JOIN employees e ON e.id=a.employee_id
        WHERE a.day BETWEEN ? AND ? AND a.day>=e.starts_on
          AND (e.ends_on IS NULL OR a.day<=e.ends_on)
    """, (start.isoformat(), min(end, as_of).isoformat())):
        attendance.setdefault(mark["employee_id"], Counter())[mark["status"]] += 1
    rows = []
    for employee in employees:
        counts = attendance.get(employee["id"], Counter())
        joined = max(start, date.fromisoformat(employee["starts_on"]))
        last = min(end, date.fromisoformat(employee["ends_on"])) if employee["ends_on"] else end
        eligible = (last - joined).days + 1
        elapsed = max(0, (min(last, as_of) - joined).days + 1)
        units = 2 * (counts["Present"] + counts["Paid leave"] + counts["Weekly off"]) + counts["Half day"]
        base = rounded(employee["rate"] * eligible, days)
        earned = rounded(employee["rate"] * units, 2 * days)
        marked_base = rounded(employee["rate"] * sum(counts.values()), days)
        net = max(0, earned + employee["bonus"] - employee["other_deduction"])
        rows.append(dict(employee, counts=counts, base=base, attendance_deduction=marked_base-earned,
                         missing=elapsed-sum(counts.values()), remaining=eligible-elapsed,
                         net=net if employee["paid_amount"] is None else employee["paid_amount"]))
    return rows


class StaffScreens:
    def staff_attendance(self, query):
        current = today()
        day = parse_day(query.get("day", [current.isoformat()])[0])
        if day > current:
            raise OperationError("Choose today or an earlier attendance date.")
        day_key = day.isoformat()
        with self.database() as db:
            employees = db.execute("""
                SELECT e.*, a.status, p.paid_on
                FROM employees e
                LEFT JOIN attendance a ON a.employee_id=e.id AND a.day=?
                LEFT JOIN payroll p ON p.employee_id=e.id AND p.month=?
                WHERE e.starts_on<=? AND (e.ends_on IS NULL OR e.ends_on>=?)
                ORDER BY e.name COLLATE NOCASE,e.id
            """, (day_key, day_key[:7], day_key, day_key)).fetchall()

            roster = db.execute("SELECT * FROM employees ORDER BY name COLLATE NOCASE,id").fetchall()
        roster_forms = "".join(f"""
            <form class="card panel" method="post" action="/staff-employees/{employee['id']}">{self.csrf_input}
            <h3>{escape(employee['name'])}</h3>
            <label>Last working day (leave blank if active)<input type="date" name="ends_on"
              min="{employee['starts_on']}" value="{employee['ends_on'] or ''}"></label>
            <button>Save work period</button></form>""" for employee in roster)
        cards = []
        for employee in employees:
            choices = "".join(f'<option{" selected" if employee["status"] == status else ""}>{status}</option>'
                              for status in STATUSES)
            actions = '<p>Payroll recorded as paid. Attendance is locked.</p>'
            if not employee["paid_on"]:
                actions = f"""
                    <form method="post" action="/staff-attendance/{employee['id']}">{self.csrf_input}
                    <input type="hidden" name="day" value="{day_key}">
                    <div class="actions"><label>Attendance<select name="status">
                    <option value="">Unmarked</option>{choices}</select></label><button>Save</button></div></form>"""
            cards.append(f'<article class="card panel"><h2>{escape(employee["name"])}</h2>'
                         f'<p>{escape(employee["role"])} · {escape(employee["status"] or "Unmarked")}</p>{actions}</article>')
        self.render("Staff attendance", f"""
            <h1>Staff attendance</h1><p><a href="/staff/payroll?month={day_key[:7]}">Open monthly payroll</a></p>
            <form class="card panel" action="/staff" method="get">
            <label>Date<input type="date" name="day" value="{day_key}" max="{current}" required></label>
            <button>View attendance</button></form>
            <details class="card panel"><summary>Add employee</summary>
            <form method="post" action="/staff-employees">{self.csrf_input}
            <div class="fields"><label>Name<input name="name" maxlength="80" required></label>
            <label>Role<input name="role" maxlength="60" required></label>
            <label>Start date<input type="date" name="starts_on" value="{current}" required></label>
            <label>Monthly salary (₹)<input type="number" name="salary" min="0.01" max="9999999.99" step="0.01" required></label></div>
            <button>Add employee</button></form></details>
            <div class="table-grid">{"".join(cards) or "<p>No employees scheduled for this date.</p>"}</div>
            <details class="card panel"><summary>Manage employee work periods</summary>
            {roster_forms or "<p>Add an employee first.</p>"}</details>""")

    def staff_payroll(self, query, download=False):
        current = today()
        month = query.get("month", [current.isoformat()[:7]])[0]
        start, _, days = month_dates(month)
        if start > current:
            raise OperationError("Choose the current month or an earlier month.")
        with self.database() as db:
            rows = payroll_rows(db, month, current)
        if download:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Month", "Employee ID", "Employee", "Role", "Monthly salary INR",
                             *STATUSES, "Unmarked elapsed days", "Upcoming days", "Attendance deduction INR",
                             "Bonus INR", "Other deductions INR", "Net INR", "Paid on"])
            for row in rows:
                writer.writerow([month, row["id"], csv_text(row["name"]), csv_text(row["role"]),
                                 f'{row["rate"]/100:.2f}', *(row["counts"][status] for status in STATUSES),
                                 row["missing"], row["remaining"], f'{row["attendance_deduction"]/100:.2f}',
                                 f'{row["bonus"]/100:.2f}', f'{row["other_deduction"]/100:.2f}',
                                 f'{row["net"]/100:.2f}', row["paid_on"] or ""])
            self.send_content(200, output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                              filename=f"tableflow-payroll-{month}.csv")
            return

        paid = sum(row["net"] for row in rows if row["paid_on"])
        ready = sum(row["net"] for row in rows if not row["paid_on"] and not row["missing"] and not row["remaining"])
        pending = sum(not row["paid_on"] and bool(row["missing"] or row["remaining"]) for row in rows)
        report = []
        for row in rows:
            counts = " · ".join(f'{label} {row["counts"][label]}' for label in STATUSES)
            note = f'Unmarked: {row["missing"]} · Upcoming: {row["remaining"]}'
            state = "Estimated pay"
            controls = ""
            if row["paid_on"]:
                state = f'Paid on {row["paid_on"]}'
            else:
                if not row["missing"] and not row["remaining"]:
                    state = "Ready to pay"
                mark_paid = ('<button name="intent" value="paid">Record completed payment</button>'
                             if state == "Ready to pay" else "")
                controls = f"""
                    <form method="post" action="/staff-payroll/{row['id']}">{self.csrf_input}
                    <input type="hidden" name="month" value="{month}">
                    <input type="hidden" name="expected_net" value="{row['net']}">
                    <div class="fields">
                    <label>Monthly salary for this month (₹)<input name="salary" type="number" min="0.01" max="9999999.99" step="0.01" value="{row['rate']/100:.2f}" required></label>
                    <label>Bonus (₹)<input name="bonus" type="number" min="0" max="9999999.99" step="0.01" value="{row['bonus']/100:.2f}"></label>
                    <label>Other deductions (₹)<input name="deduction" type="number" min="0" max="9999999.99" step="0.01" value="{row['other_deduction']/100:.2f}"></label></div>
                    <button name="intent" value="save">Save payroll changes</button>{mark_paid}</form>"""
            report.append(f"""
                <section class="card panel"><h2>{escape(row['name'])}</h2><p>{escape(row['role'])}</p>
                <p>{counts}</p><p>{note}</p>
                <p>Prorated base {money(row['base'])} · Attendance deduction {money(row['attendance_deduction'])}
                · Bonus {money(row['bonus'])} · Other deductions {money(row['other_deduction'])}</p>
                <p class="total">{state} · {money(row['net'])}</p>{controls}</section>""")
        self.render("Monthly payroll", f"""
          <h1>Monthly payroll</h1><p><a href="/staff">Staff attendance</a></p>
          <form class="card panel" action="/staff/payroll" method="get">
          <label>Month<input type="month" name="month" value="{month}" max="{current.isoformat()[:7]}" required></label>
          <button>View payroll</button></form>
          <div class="stats"><div class="card stat"><small>Paid</small><strong>{money(paid)}</strong></div>
          <div class="card stat"><small>Ready to pay</small><strong>{money(ready)}</strong></div>
          <div class="card stat"><small>Incomplete records</small><strong>{pending}</strong></div></div>
          <p>Salary is prorated across {days} calendar days. Present, paid leave and weekly off
          count as one paid day. Half day counts as half. Complete attendance for the employee’s entire work period in the month before recording payment.</p>
          <p><a href="/staff/payroll.csv?month={month}">Download payroll CSV</a></p>
          {"".join(report) or "<p>No staff records for this month.</p>"}""")

    def save_staff(self, db, section, identifier, form):
        if section == "staff-employees" and not identifier:
            name, role = (form.get(key, "").strip() for key in ("name", "role"))
            start = parse_day(form.get("starts_on"))
            salary = amount(form.get("salary", ""))
            if not 1 <= len(name) <= 80 or not 1 <= len(role) <= 60 or not salary:
                raise OperationError("Enter an employee name, role and monthly salary above zero.")
            db.execute("INSERT INTO employees(name,role,starts_on,monthly_salary) VALUES (?,?,?,?)",
                       (name, role, start.isoformat(), salary))
            return "/staff"
        employee = db.execute("SELECT * FROM employees WHERE id=?", (identifier,)).fetchone()
        if not employee:
            raise OperationError("Employee not found.")
        if section == "staff-employees":
            value = form.get("ends_on", "")
            end = parse_day(value).isoformat() if value else None
            old_end = employee["ends_on"]
            if end and end < employee["starts_on"]:
                raise OperationError("Last working day must be on or after the start date.")
            if end != old_end:
                first_change = min(day for day in (end, old_end) if day)
                locked = db.execute("""
                    SELECT 1 FROM payroll WHERE employee_id=? AND month>=? AND paid_on IS NOT NULL
                """, (identifier, first_change[:7])).fetchone()
                if locked:
                    raise OperationError("This work period includes payroll already recorded as paid.")
                if end and db.execute("SELECT 1 FROM attendance WHERE employee_id=? AND day>?", (identifier, end)).fetchone():
                    raise OperationError("Clear attendance after the last working day before saving.")
                db.execute("UPDATE employees SET ends_on=? WHERE id=?", (end, identifier))
            return "/staff"
        if section == "staff-attendance":
            day = parse_day(form.get("day")).isoformat()
            if (day > today().isoformat() or day < employee["starts_on"]
                    or (employee["ends_on"] and day > employee["ends_on"])):
                raise OperationError("Choose an elapsed date within the employee’s work period.")
            if self.payroll_paid(db, identifier, day[:7]):
                raise OperationError("Attendance for paid payroll is locked.")
            status = form.get("status", "")
            if not status:
                db.execute("DELETE FROM attendance WHERE employee_id=? AND day=?", (identifier, day))
            elif status in STATUSES:
                db.execute("""
                    INSERT INTO attendance(employee_id,day,status) VALUES (?,?,?)
                    ON CONFLICT(employee_id,day) DO UPDATE SET status=excluded.status
                """, (identifier, day, status))
            else:
                raise OperationError("Choose an attendance status.")
            return f"/staff?day={day}"

        month = form.get("month", "")
        start, end, _ = month_dates(month)
        if (start > today() or end.isoformat() < employee["starts_on"]
                or (employee["ends_on"] and employee["ends_on"] < start.isoformat())):
            raise OperationError("Choose an eligible payroll month.")
        if self.payroll_paid(db, identifier, month):
            return f"/staff/payroll?month={month}"
        rate = amount(form.get("salary", ""))
        bonus, deduction = (amount(form.get(key, ""), optional=True) for key in ("bonus", "deduction"))
        if not rate:
            raise OperationError("Enter a monthly salary above zero.")
        db.execute("""
            INSERT INTO payroll(employee_id,month,bonus,other_deduction,monthly_salary) VALUES (?,?,?,?,?)
            ON CONFLICT(employee_id,month) DO UPDATE SET bonus=excluded.bonus,
                other_deduction=excluded.other_deduction,monthly_salary=excluded.monthly_salary
        """, (identifier, month, bonus, deduction, rate))
        if form.get("intent") == "paid":
            row = next(row for row in payroll_rows(db, month, today()) if row["id"] == employee["id"])
            if row["missing"] or row["remaining"]:
                raise OperationError("Complete the month’s attendance before recording payment.")
            if form.get("expected_net") != str(row["net"]):
                raise OperationError("The amount changed. Save payroll changes and review the updated total first.")
            db.execute("UPDATE payroll SET paid_amount=?,paid_on=? WHERE employee_id=? AND month=?",
                       (row["net"], today().isoformat(), identifier, month))
        return f"/staff/payroll?month={month}"

    def payroll_paid(self, db, identifier, month):
        return db.execute("SELECT paid_on FROM payroll WHERE employee_id=? AND month=? AND paid_on IS NOT NULL",
                          (identifier, month)).fetchone() is not None
