"""Monthly sales reports and inventory records for the staff dashboard."""

import csv
import io
import re
from datetime import date, datetime, timedelta
from html import escape

import operations as ops
from staff import amount, month_dates

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    low_stock INTEGER NOT NULL CHECK(low_stock >= 0)
);
CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES inventory_items(id),
    kind TEXT NOT NULL CHECK(kind IN ('Opening', 'Purchase', 'Usage', 'Waste')),
    delta INTEGER NOT NULL,
    cost INTEGER NOT NULL CHECK(cost >= 0),
    note TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS inventory_movements_item ON inventory_movements(item_id);
"""
UNITS = ("kg", "g", "L", "mL", "pcs", "packs", "boxes", "bottles")
MOVEMENTS = {"Purchase": 1, "Usage": -1, "Waste": -1}


def parse_quantity(value):
    if not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,3})?", value):
        raise ops.OperationError("Enter a quantity with up to three decimal places.")
    whole, _, fraction = value.partition(".")
    return int(whole) * 1000 + int(fraction.ljust(3, "0"))


def quantity(value):
    whole, fraction = divmod(abs(value), 1000)
    return f'{"−" if value < 0 else ""}{whole}.{fraction:03}'.rstrip("0").rstrip(".")


def saved_movement(db, item_id, kind, delta, cost=0, note=""):
    db.execute("""
        INSERT INTO inventory_movements(item_id,kind,delta,cost,note,recorded_at)
        VALUES (?,?,?,?,?,?)
    """, (item_id, kind, delta, cost, note, ops.now()))


def save_inventory(db, section, identifier, form):
    if section == "inventory-items" and identifier is None:
        name = form.get("name", "").strip()
        category = form.get("category", "").strip()
        unit = form.get("unit", "")
        if not name or len(name) > 80 or not category or len(category) > 50 or unit not in UNITS:
            raise ops.OperationError("Enter an item name, category and unit.")
        opening = parse_quantity(form.get("opening", "0"))
        threshold = parse_quantity(form.get("threshold", "0"))
        item_id = db.execute(
            "INSERT INTO inventory_items(name,category,unit,low_stock) VALUES (?,?,?,?)",
            (name, category, unit, threshold),
        ).lastrowid
        if opening:
            saved_movement(db, item_id, "Opening", opening)
        return "/inventory"

    item = db.execute("SELECT id FROM inventory_items WHERE id=?", (identifier,)).fetchone()
    if not item:
        raise ops.OperationError("Select an inventory item.")
    if section == "inventory-items":
        threshold = parse_quantity(form.get("threshold", ""))
        db.execute("UPDATE inventory_items SET low_stock=? WHERE id=?", (threshold, identifier))
        return "/inventory"

    kind = form.get("kind", "")
    units = parse_quantity(form.get("quantity", ""))
    note = form.get("note", "").strip()
    if kind not in MOVEMENTS or not units or len(note) > 200:
        raise ops.OperationError("Select a movement type and enter a positive quantity and a note of up to 200 characters.")
    cost = amount(form.get("cost", ""), optional=True)
    if cost and kind != "Purchase":
        raise ops.OperationError("Enter a cost only for purchases.")
    delta = units * MOVEMENTS[kind]
    current = db.execute(
        "SELECT COALESCE(SUM(delta),0) FROM inventory_movements WHERE item_id=?", (identifier,)
    ).fetchone()[0]
    if current + delta < 0:
        raise ops.OperationError("This movement exceeds the available stock.")
    saved_movement(db, identifier, kind, delta, cost, note)
    return f"/inventory?item={identifier}"


class ManagementScreens:
    def monthly_sales(self, query, download=False):
        today = datetime.now(ops.TIME_ZONE).date()
        month = query.get("month", [today.strftime("%Y-%m")])[0]
        first, last, days = month_dates(month)
        if first > today:
            raise ops.OperationError("Select this month or an earlier month.")
        start = ops.daily_bounds(first)[0]
        end = ops.daily_bounds(last)[1]
        with self.database() as db:
            payments = db.execute("""
                SELECT p.paid_at,p.method,p.total,COALESCE(b.tip,0) AS tip
                FROM payments p LEFT JOIN bill_preferences b ON b.visit_id=p.visit_id
                WHERE p.paid_at>=? AND p.paid_at<?
            """, (start, end)).fetchall()
            visits = db.execute("""
                SELECT guests,opened_at FROM visits WHERE opened_at>=? AND opened_at<?
            """, (start, end)).fetchall()
        daily = {first + timedelta(days=i): [0, 0, 0, 0, 0, 0] for i in range(days)}
        methods = dict.fromkeys(ops.METHODS, (0, 0))
        for payment in payments:
            day = datetime.fromisoformat(payment["paid_at"]).astimezone(ops.TIME_ZONE).date()
            summary = daily[day]
            summary[0] += payment["total"] - payment["tip"]
            summary[1] += payment["tip"]
            summary[2] += payment["total"]
            summary[3] += 1
            count, collected = methods.get(payment["method"], (0, 0))
            methods[payment["method"]] = count + 1, collected + payment["total"]
        for visit in visits:
            day = datetime.fromisoformat(visit["opened_at"]).astimezone(ops.TIME_ZONE).date()
            daily[day][4] += 1
            daily[day][5] += visit["guests"]
        totals = list(map(sum, zip(*daily.values())))
        columns = ("Date", "Sales excluding tips", "Tips", "Collected", "Payments", "Visits", "Guests")
        if download:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(columns)
            writer.writerows((day.isoformat(), *(f"{value / 100:.2f}" for value in data[:3]), *data[3:])
                             for day, data in daily.items())
            writer.writerow(("Total", *(f"{value / 100:.2f}" for value in totals[:3]), *totals[3:]))
            writer.writerow(())
            writer.writerow(("Method", "Payments", "Collected"))
            writer.writerows((method, count, f"{collected / 100:.2f}")
                             for method, (count, collected) in methods.items())
            self.send_content(200, output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", f"sales-{month}.csv")
            return
        stats = "".join(
            f'<div class="card stat"><small>{label}</small><strong>{value}</strong></div>'
            for label, value in zip(columns[1:], [*(ops.money(value) for value in totals[:3]), *totals[3:]])
        )
        method_rows = "".join(f"<tr><td>{escape(method)}</td><td>{count}</td><td>{ops.money(collected)}</td></tr>"
                              for method, (count, collected) in methods.items())
        days_html = "".join(
            f'<tr><td><a href="/sales?day={day}">{day:%d %b}</a></td>'
            + "".join(f"<td>{value}</td>" for value in [*(ops.money(value) for value in data[:3]), *data[3:]])
            + "</tr>"
            for day, data in daily.items() if day <= today
        )
        headers = "".join(f"<th>{label}</th>" for label in columns)
        self.render("Monthly sales", f"""
            <h1>Monthly sales</h1><p>Payments are counted on the date received in India Standard Time. Visits and guests are counted on the date the table visit opened.</p>
            <form class="card panel" method="get"><label>Month<input type="month" name="month" value="{month}" max="{today:%Y-%m}" required></label><button>View month</button></form>
            <p><a href="/sales">Daily sales &amp; checkout</a> · <a href="/sales/monthly.csv?month={month}">Download CSV</a></p>
            <div class="stats">{stats}</div>
            <section class="card panel"><h2>Payment methods</h2><table><thead><tr><th>Method</th><th>Payments</th><th>Collected</th></tr></thead><tbody>{method_rows}</tbody></table></section>
            <section class="card panel"><h2>Daily breakdown</h2><div style="overflow-x:auto"><table><thead><tr>{headers}</tr></thead><tbody>{days_html}</tbody></table></div></section>
        """)

    def inventory(self, query):
        selected = query.get("item", [""])[0]
        if selected and not re.fullmatch(r"[0-9]{1,18}", selected):
            raise ops.OperationError("Select a valid inventory item.")
        today = datetime.now(ops.TIME_ZONE).date()
        month = query.get("month", [today.strftime("%Y-%m")])[0]
        first, last, _ = month_dates(month)
        if first > today:
            raise ops.OperationError("Select this month or an earlier month.")
        start = ops.daily_bounds(first)[0]
        end = ops.daily_bounds(last)[1]
        with self.database() as db:
            items = db.execute("""
                SELECT i.*, COALESCE(SUM(m.delta),0) AS stock FROM inventory_items i
                LEFT JOIN inventory_movements m ON m.item_id=i.id
                GROUP BY i.id ORDER BY i.category COLLATE NOCASE,i.name
            """).fetchall()
            movements = db.execute("""
                SELECT m.*,i.name,i.unit FROM inventory_movements m
                JOIN inventory_items i ON i.id=m.item_id
                WHERE m.recorded_at>=? AND m.recorded_at<? AND (?='' OR m.item_id=?)
                ORDER BY m.id DESC
            """, (start, end, selected, selected)).fetchall()
        cards = []
        for item in items:
            item_id = item["id"]
            unit = escape(item["unit"])
            flag = "<strong> · Low stock</strong>" if item["stock"] <= item["low_stock"] else ""
            cards.append(f"""
                <details class="card panel"><summary>{escape(item["name"])} · {escape(item["category"])} · {quantity(item["stock"])} {unit}{flag}</summary>
                <p><a href="/inventory?item={item_id}&amp;month={month}#stock-history">View movement history</a></p>
                <form method="post" action="/inventory-movements/{item_id}">{self.csrf_input}
                    <label>Movement<select name="kind"><option>Purchase</option><option>Usage</option><option>Waste</option></select></label>
                    <label>Quantity ({unit})<input name="quantity" type="number" min="0.001" step="0.001" required></label>
                    <label>Total purchase cost (₹, optional)<input name="cost" type="number" min="0" step="0.01"></label>
                    <label>Note<input name="note" maxlength="200"></label><button>Record movement</button>
                </form>
                <form method="post" action="/inventory-items/{item_id}">{self.csrf_input}
                    <label>Low-stock threshold ({unit})<input name="threshold" type="number" value="{quantity(item["low_stock"])}" min="0" step="0.001" required></label><button>Save threshold</button>
                </form></details>""")
        rows = "".join(
            f'<tr><td>{ops.local_time(row["recorded_at"])}</td><td>{escape(row["name"])}</td><td>{row["kind"]}</td>'
            f'<td>{quantity(row["delta"])} {escape(row["unit"])}</td><td>{ops.money(row["cost"]) if row["cost"] else "—"}</td>'
            f'<td>{escape(row["note"])}</td></tr>' for row in movements
        )
        options = "".join(f"<option>{unit}</option>" for unit in UNITS)
        filters = "".join(
            f'<option value="{item["id"]}" {"selected" if str(item["id"]) == selected else ""}>{escape(item["name"])}</option>'
            for item in items
        )
        purchase_total = ops.money(sum(row["cost"] for row in movements))
        self.render("Inventory", f"""
            <h1>Inventory</h1><p>Track groceries, ingredients, beverages and supplies. Stock changes when staff record purchases, usage or waste.</p>
            <details class="card panel"><summary>Add inventory item</summary>
            <form method="post" action="/inventory-items">{self.csrf_input}
                <label>Item name<input name="name" maxlength="80" required></label>
                <label>Category<input name="category" maxlength="50" placeholder="Groceries" required></label>
                <label>Unit<select name="unit">{options}</select></label>
                <label>Opening stock<input name="opening" type="number" value="0" min="0" step="0.001" required></label>
                <label>Low-stock threshold<input name="threshold" type="number" value="0" min="0" step="0.001" required></label>
                <button>Add item</button></form></details>
            <h2>Current stock</h2>{"".join(cards) or "<p>No inventory items yet.</p>"}
            <section class="card panel" id="stock-history"><h2>Movement history</h2>
            <form method="get" action="/inventory#stock-history">
                <label>Month<input name="month" type="month" value="{month}" max="{today:%Y-%m}" required></label>
                <label>Item<select name="item"><option value="">All items</option>{filters}</select></label><button>View history</button>
            </form>
            <p>Recorded purchase costs for this selection <strong>{purchase_total}</strong></p>
            <div style="overflow-x:auto"><table><thead><tr><th>Recorded</th><th>Item</th><th>Movement</th><th>Quantity</th><th>Cost</th><th>Note</th></tr></thead>
            <tbody>{rows or '<tr><td colspan="6">No movements for this selection.</td></tr>'}</tbody></table></div></section>
        """)
