import argparse
import base64
import binascii
import html
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import operations as ops
import online
from screens import Screens
from management import SCHEMA as MANAGEMENT_SCHEMA, ManagementScreens, save_inventory
from staff import SCHEMA as STAFF_SCHEMA, StaffScreens, amount
from qr import png as qr_png

ROOT = Path(__file__).resolve().parent
STATES = ("Available", "Occupied", "Cleaning")
ADMIN_TOKEN = secrets.token_urlsafe(32)
DISHES = (
    ("Paneer Tikka Masala", "Mains", "Chargrilled paneer in a creamy tomato and onion gravy.", 269, "paneer"),
    ("Margherita Pizza", "Mains", "Tomato sauce, melted mozzarella and fresh basil on a thin crust.", 299, "pizza"),
    ("Iced Latte", "Drinks", "Double espresso poured over chilled milk and ice.", 169, "latte"),
    ("Chocolate Brownie", "Desserts", "Warm dark chocolate brownie with a scoop of vanilla ice cream.", 189, "brownie"),
)


@contextmanager
def database():
    connection = sqlite3.connect(os.environ.get("TABLEFLOW_DB", ROOT / "tableflow.sqlite3"))
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize():
    with database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS dining_tables (
            id INTEGER PRIMARY KEY, name TEXT COLLATE NOCASE UNIQUE NOT NULL,
            seats INTEGER NOT NULL, status TEXT NOT NULL, token TEXT UNIQUE NOT NULL
        )""")
        if not db.execute("SELECT 1 FROM dining_tables LIMIT 1").fetchone():
            db.executemany("INSERT INTO dining_tables VALUES (NULL, ?, ?, ?, ?)", [
                (f"Table {number:02}", seats, "Available", secrets.token_urlsafe(12))
                for number, seats in enumerate((2, 2, 4, 4, 6, 6), start=1)
            ])

        db.executescript(ops.SCHEMA + STAFF_SCHEMA + online.SCHEMA + MANAGEMENT_SCHEMA)
        if not db.execute("SELECT 1 FROM menu_items LIMIT 1").fetchone():
            db.executemany("INSERT INTO menu_items(name,category,description,price,photo) VALUES(?,?,?,?,?)",
                          [(name, category, description, price * 100, photo)
                           for name, category, description, price, photo in DISHES])


def page(title, body, variant="", auto_refresh=False):
    refresh = '<meta http-equiv="refresh" content="8">' if auto_refresh else ""
    nav = ('<nav><a href="/">Tables</a><a href="/floor">Floor</a><a href="/orders">Orders</a><a href="/kitchen">Kitchen</a>'
           '<a href="/sales">Sales &amp; checkout</a><a href="/inventory">Inventory</a><a href="/manage-menu">Menu</a><a href="/staff">Staff</a><a href="/waiter">Waiter calls <span class="waiter-badge" data-waiter-count aria-live="polite" hidden></span></a></nav>') if not variant else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
    <title>{html.escape(title)} · TableFlow</title>
    <link rel="stylesheet" href="/static/style.css">{'<script defer src="/static/staff.js"></script>' if not variant else ''}</head>
    <body class="{variant}"><header><a class="brand" href="{"/" if not variant else "#"}">
    <span class="brand-logo"><img src="/static/amveos.png" alt="Amveos" width="904" height="384"></span></a>{nav}</header>
    <main>{body}</main></body></html>"""


def options(selected):
    return "".join(
        f'<option{" selected" if state == selected else ""}>{state}</option>' for state in STATES
    )


def table_card(table):
    name = html.escape(table["name"])
    token = table["token"]
    status = table["status"]
    waiter = '<p><a href="/waiter">Waiter requested</a></p>' if table["waiter_pending"] else ""
    billing = '<p>Bill paid · release table when guests leave</p>' if status == "Occupied" and table["bill_paid"] else ""
    return f"""<article class="card table-card">
    <div class="table-heading"><h2>{name}</h2><span class="status {status.lower()}">{status}</span></div>
    <p>{table["seats"]} seats</p>{billing}{waiter}
    <form method="post" action="/tables/{table["id"]}">
      <input type="hidden" name="csrf" value="{ADMIN_TOKEN}">
      <div class="fields"><label>Name<input required name="name" maxlength="50" value="{name}"></label>
      <label>Seats<input required name="seats" type="number" min="1" max="100" value="{table["seats"]}"></label></div>
      <div class="actions"><label>Status<select name="status">{options(status)}</select></label><button>Save</button></div>
    </form>
    <div class="links"><a href="/qr/{token}">View QR code</a><a href="/menu/{token}">Preview menu ↗</a></div>
    </article>"""


class Handler(Screens, StaffScreens, ManagementScreens, BaseHTTPRequestHandler):
    def send_content(self, status, content, mime="text/html; charset=utf-8", filename=None):
        if isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; img-src 'self' data: https:; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    def error_page(self, status, message, link="/", guest=False):
        self.send_content(status, page("TableFlow", f'<h1>{html.escape(message)}</h1>'
                                     f'<p><a href="{html.escape(link)}">Go back</a></p>', "guest" if guest else ""))

    database = staticmethod(database)
    csrf_input = f'<input type="hidden" name="csrf" value="{ADMIN_TOKEN}">'

    def log_request(self, code="-", size="-"):
        pass

    def render(self, title, body, variant="", auto_refresh=False):
        self.send_content(200, page(title, body, variant, auto_refresh))

    def redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def is_staff(self):
        if self.client_address[0] == "127.0.0.1":
            return True
        key = getattr(self.server, "staff_key", "")
        scheme, _, value = self.headers.get("Authorization", "").partition(" ")
        if not key or scheme.lower() != "basic":
            return False
        try:
            credentials = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            return False
        return secrets.compare_digest(credentials, f"staff:{key}".encode())

    def staff_only(self):
        if self.is_staff():
            return True
        if getattr(self.server, "staff_key", ""):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="TableFlow staff"')
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        else:
            self.send_content(403, "Open the staff dashboard on the computer running TableFlow.", "text/plain")
        return False

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        asset_types = {"/static/style.css": "text/css; charset=utf-8",
                       "/static/food.jpg": "image/jpeg", "/static/amveos.png": "image/png",
                       "/static/menu.js": "text/javascript; charset=utf-8",
                       "/static/staff.js": "text/javascript; charset=utf-8"}
        if path in asset_types:
            asset = ROOT / path.lstrip("/")
            if not asset.is_file():
                self.send_error(404)
                return
            self.send_content(200, asset.read_bytes(), asset_types[path])
            return
        if path in ("/staff", "/staff/payroll", "/staff/payroll.csv"):
            if self.staff_only():
                query = parse_qs(url.query)
                try:
                    if path == "/staff":
                        self.staff_attendance(query)
                    else:
                        self.staff_payroll(query, download=path.endswith(".csv"))
                except ops.OperationError as error:
                    self.error_page(400, str(error), "/staff")
            return
        if path in ("/sales/monthly", "/sales/monthly.csv", "/inventory"):
            if self.staff_only():
                query = parse_qs(url.query)
                try:
                    if path == "/inventory":
                        self.inventory(query)
                    else:
                        self.monthly_sales(query, download=path.endswith(".csv"))
                except ops.OperationError as error:
                    self.error_page(400, str(error), "/inventory" if path == "/inventory" else "/sales/monthly")
            return
        if path == "/sales":
            if self.staff_only():
                try:
                    self.sales(parse_qs(url.query))
                except ops.OperationError as error:
                    self.error_page(400, str(error), "/sales")
            return
        if path == "/waiter-count":
            if self.staff_only():
                with database() as db:
                    count = db.execute("SELECT COUNT(*) FROM waiter_calls WHERE resolved_at IS NULL").fetchone()[0]
                self.send_content(200, str(count), "text/plain; charset=utf-8")
            return
        if path == "/kitchen":
            if self.staff_only():
                self.kitchen(parse_qs(url.query).get("station", [""])[0])
            return
        pages = {"/floor": self.floor, "/orders": self.order_inbox, "/manage-menu": self.menu_admin, "/waiter": self.waiter_calls}
        if path in pages:
            if self.staff_only():
                pages[path]()
            return
        if match := re.fullmatch(r"/checkout/([0-9]{1,18})", path):
            if self.staff_only():
                self.checkout(int(match[1]))
            return
        if match := re.fullmatch(r"/invoice/([A-Za-z0-9_-]{32})/photo", path):
            if self.staff_only():
                self.invoice_photo(match[1])
            return
        if match := re.fullmatch(r"/receipt/([A-Za-z0-9_-]{32})", path):
            self.guest_receipt(match[1])
            return
        if match := re.fullmatch(r"/(visit|invoice)/([A-Za-z0-9_-]{32})", path):
            kind, token = match.groups()
            if kind == "invoice":
                if self.staff_only():
                    self.invoice(token)
                return
            with database() as db:
                visit = ops.find_visit(db, token)
            if visit:
                self.visit_screen(visit)
            else:
                self.send_error(404)
            return
        if path == "/" and self.staff_only():
            self.dashboard()
        elif match := re.fullmatch(r"/(menu|qr)/([A-Za-z0-9_-]+)", path):
            section, token = match.groups()
            if section == "qr" and not self.staff_only():
                return
            with database() as db:
                table = db.execute("SELECT * FROM dining_tables WHERE token=?", (token,)).fetchone()
            if table is None:
                self.send_error(404, "Table not found")
            elif section == "menu":
                query = parse_qs(url.query)
                self.order_menu(table, query.get("category", ["All"])[0], query.get("visit", [""])[0])
            else:
                self.show_qr(table)
        elif path != "/":
            self.send_error(404)

    def dashboard(self):
        with database() as db:
            tables = db.execute("""
                SELECT d.*, EXISTS(SELECT 1 FROM waiter_calls w WHERE w.table_id=d.id AND w.resolved_at IS NULL)
                    AS waiter_pending, COALESCE((
                    SELECT p.visit_id IS NOT NULL FROM visits v LEFT JOIN payments p ON p.visit_id=v.id
                    WHERE v.table_id=d.id
                    ORDER BY v.id DESC LIMIT 1
                ),0) AS bill_paid FROM dining_tables d
                ORDER BY d.id
            """).fetchall()
        cards = "".join(table_card(table) for table in tables)
        stats = "".join(
            f'<div class="card stat"><small>{state}</small><strong>{sum(t["status"] == state for t in tables)}</strong></div>'
            for state in STATES
        )
        self.send_content(200, page("Tables", f"""
          <section class="intro"><h1>Dining tables</h1><p>Manage seating and open each table’s QR menu.</p></section>
          <div class="stats">{stats}</div>
          <details class="card add"><summary>Add a table</summary>
          <form method="post" action="/tables">
            <input type="hidden" name="csrf" value="{ADMIN_TOKEN}">
            <div class="fields"><label>Name<input name="name" maxlength="50" placeholder="Patio 01" required></label>
            <label>Seats<input type="number" name="seats" value="4" min="1" max="100" required></label></div>
            <input type="hidden" name="status" value="Available"><button>Add table</button>
          </form></details>
          <div class="table-grid">{cards}</div>"""))

    def show_qr(self, table):
        base = self.server.menu_base
        if not base:
            self.error_page(503, "The customer link is unavailable. Wait for Customer HTTPS base in Terminal.")
            return
        link = f"{base}/menu/{table['token']}"
        name = html.escape(table["name"])
        try:
            encoded = base64.b64encode(qr_png(link)).decode()
        except ValueError:
            self.render("Table menu link", f'<h1>{name}</h1><p><a href="{html.escape(link)}">Open customer menu</a></p>')
            return
        url = html.escape(link)
        self.send_content(200, page("Table QR code", f"""
          <a class="back" href="/">← Dining tables</a>
          <section class="card qr"><h1>{name}</h1><p>Scan to open the menu</p>
            <img width="232" height="232" src="data:image/png;base64,{encoded}" alt="Menu QR code for {name}">
            <p><a href="{url}">{url}</a></p>
            <p><a download="table-{table['id']}.png" href="data:image/png;base64,{encoded}">Download QR code</a></p>
          </section>"""))

    def do_POST(self):
        path = urlsplit(self.path).path
        guest_match = re.fullmatch(r"/(order|call-waiter|start-upi|report-upi|start-online|bill-preferences)/([A-Za-z0-9_-]{16,32})", path)
        staff_match = re.fullmatch(r"/(tables|kitchen|accept-order|resolve-waiter|reopen-upi|cancel-ticket|correct-payment|checkout|manage-menu|staff-employees|staff-attendance|staff-payroll|inventory-items|inventory-movements)(?:/([0-9]{1,18}))?|/(whatsapp)/([A-Za-z0-9_-]{32})", path)
        if not guest_match and not self.staff_only():
            return
        if not guest_match and not staff_match:
            self.send_error(404)
            return
        groups = (guest_match or staff_match).groups()
        section, identifier = groups[:2] if groups[0] else groups[2:]
        back = {"order": f"/menu/{identifier}",
                "checkout": f"/checkout/{identifier}", "kitchen": "/kitchen",
                "call-waiter": f"/menu/{identifier}", "start-upi": f"/visit/{identifier}", "start-online": f"/visit/{identifier}", "bill-preferences": f"/visit/{identifier}", "report-upi": f"/visit/{identifier}",
                "resolve-waiter": "/waiter", "reopen-upi": f"/checkout/{identifier}", "cancel-ticket": "/kitchen", "accept-order": "/orders", "correct-payment": "/sales",
                "manage-menu": "/manage-menu", "whatsapp": f"/invoice/{identifier}"}.get(section, "/")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError
            form = {key: values[0] for key, values in parse_qs(
                self.rfile.read(length).decode(), max_num_fields=4096).items()}
        except ValueError:
            self.error_page(400, "The form could not be read.", back, bool(guest_match))
            return
        if not guest_match and form.get("csrf") != ADMIN_TOKEN:
            self.error_page(403, "Refresh the dashboard and try again.")
            return
        if section.startswith("staff-"):
            back = "/staff/payroll" if section == "staff-payroll" else "/staff"
        if section.startswith("inventory-"):
            back = "/inventory"
        receipt = None
        try:
            if section == "whatsapp":
                self.process_whatsapp(identifier)
                self.redirect(back)
                return
            with database() as db:
                db.execute("BEGIN IMMEDIATE")
                destination = self.save_action(db, section, identifier, form)
                if section == "checkout":
                    receipt = destination.rsplit("/", 1)[1]
        except ops.OperationError as error:
            self.error_page(400, str(error), back, bool(guest_match))
            return
        except sqlite3.IntegrityError:
            self.error_page(409, "That change conflicts with existing data. Refresh and try again.", back, bool(guest_match))
            return
        if receipt:
            self.process_whatsapp(receipt)
        if section == "kitchen" and form.get("current") == "Ready":
            self.send_served_notification(identifier)
        self.redirect(destination)

    def save_action(self, db, section, identifier, form):
        if section.startswith("inventory-"):
            return save_inventory(db, section, identifier, form)
        if section == "call-waiter":
            table = db.execute("SELECT * FROM dining_tables WHERE token=?", (identifier,)).fetchone()
            if not table or table["status"] == "Cleaning":
                raise ops.OperationError("Waiter requests are unavailable for this table.")
            db.execute("""
                INSERT INTO waiter_calls(table_id,called_at) VALUES (?,?)
                ON CONFLICT(table_id) DO UPDATE SET called_at=excluded.called_at,resolved_at=NULL
                WHERE waiter_calls.resolved_at IS NOT NULL
            """, (table["id"], datetime.now(timezone.utc).isoformat(timespec="microseconds")))
            token = form.get("visit", "")
            visit = ops.find_visit(db, token) if token else None
            return f"/visit/{token}" if visit and visit["table_id"] == table["id"] else f"/menu/{identifier}"
        if section == "resolve-waiter":
            db.execute("UPDATE waiter_calls SET resolved_at=? WHERE table_id=? AND resolved_at IS NULL AND called_at=?",
                       (ops.now(), identifier, form.get("called_at")))
            return "/waiter"
        if section == "bill-preferences":
            visit = ops.find_visit(db, identifier)
            if (not visit or visit["paid"] or not visit["has_items"]
                or db.execute("SELECT 1 FROM upi_requests WHERE visit_id=?", (visit["id"],)).fetchone()
                or db.execute("SELECT 1 FROM online_checkouts WHERE visit_id=? AND state='pending'", (visit["id"],)).fetchone()):
                raise ops.OperationError("The bill can no longer be changed.")
            ops.require_approved_orders(db, visit["id"])
            split = form.get("split_count", "1")
            if not re.fullmatch(r"[0-9]{1,2}", split) or not 1 <= int(split) <= 20:
                raise ops.OperationError("Choose between 1 and 20 people.")
            tip = amount(form.get("tip", ""), optional=True)
            if tip > ops.total_for(ops.bill_items(db, visit["id"], include_tip=False)):
                raise ops.OperationError("The tip cannot exceed the order subtotal.")
            db.execute("""
                INSERT INTO bill_preferences VALUES (?,?,?) ON CONFLICT(visit_id)
                DO UPDATE SET tip=excluded.tip,split_count=excluded.split_count
            """, (visit["id"], tip, int(split)))
            return f"/visit/{identifier}"
        if section == "start-online":
            online.reserve(db, ops.find_visit(db, identifier))
            return f"/visit/{identifier}"
        if section == "start-upi":
            visit = ops.find_visit(db, identifier)
            if not visit:
                raise ops.OperationError("UPI is unavailable for this order.")
            ops.request_upi(db, visit)
            return f"/visit/{identifier}"
        if section == "report-upi":
            visit = ops.find_visit(db, identifier)
            if not visit or visit["paid"]:
                raise ops.OperationError("This UPI payment is no longer pending.")
            request = db.execute("SELECT reference FROM upi_requests WHERE visit_id=?", (visit["id"],)).fetchone()
            if not request or request["reference"] != form.get("upi_reference"):
                raise ops.OperationError("The UPI request changed. Open your order again.")
            db.execute("INSERT INTO upi_payment_reports VALUES (?,?) ON CONFLICT(reference) DO NOTHING",
                       (request["reference"], ops.now()))
            return f"/visit/{identifier}"
        if section == "reopen-upi":
            db.execute("""
                DELETE FROM upi_requests WHERE visit_id=? AND reference=?
                AND NOT EXISTS(SELECT 1 FROM payments WHERE visit_id=?)
            """, (identifier, form.get("upi_reference"), identifier))
            return f"/checkout/{identifier}"
        if section == "order":
            table = db.execute("SELECT * FROM dining_tables WHERE token=?", (identifier,)).fetchone()
            if not table:
                raise ops.OperationError("Table not found.")
            return "/visit/" + ops.place_order(db, table, form)
        if section == "checkout":
            row = db.execute("SELECT token FROM visits WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise ops.OperationError("Bill not found.")
            if db.execute("SELECT 1 FROM online_checkouts WHERE visit_id=? AND state='pending'", (identifier,)).fetchone():
                raise ops.OperationError("Wait for automatic online payment verification before closing this bill.")
            return "/invoice/" + ops.confirm_payment(db, ops.find_visit(db, row["token"]), form)
        if section == "accept-order":
            changed = db.execute("UPDATE tickets SET state='New' WHERE id=? AND state='Awaiting approval'",
                                 (identifier,)).rowcount
            if changed:
                db.execute("INSERT INTO kitchen_releases VALUES (?,?)", (identifier, ops.now()))
            return "/orders"
        if section == "kitchen":
            current = form.get("current")
            if current not in ops.KITCHEN_STAGES[:-1]:
                raise ops.OperationError("Choose a valid kitchen status.")
            following = ops.KITCHEN_STAGES[ops.KITCHEN_STAGES.index(current) + 1]
            changed = db.execute("UPDATE tickets SET state=? WHERE id=? AND state=?",
                                 (following, identifier, current)).rowcount
            if changed and following == "Served":
                db.execute("UPDATE served_notifications SET state='Pending' WHERE ticket_id=?",
                           (identifier,))
            return "/kitchen"
        if section == "cancel-ticket":
            reason = form.get("reason", "").strip()
            if not 1 <= len(reason) <= 160:
                raise ops.OperationError("Enter a cancellation reason.")
            changed = db.execute("""
                UPDATE tickets SET state='Cancelled' WHERE id=? AND state IN ('Awaiting approval','New','Preparing','Ready')
                AND NOT EXISTS(SELECT 1 FROM payments p WHERE p.visit_id=tickets.visit_id)
                AND NOT EXISTS(SELECT 1 FROM upi_requests u WHERE u.visit_id=tickets.visit_id)
                AND NOT EXISTS(SELECT 1 FROM online_checkouts o WHERE o.visit_id=tickets.visit_id AND o.state='pending')
            """, (identifier,)).rowcount
            if changed:
                db.execute("INSERT INTO ticket_cancellations VALUES (?,?,?)",
                           (identifier, reason, ops.now()))
                db.execute("UPDATE served_notifications SET state='Cancelled' WHERE ticket_id=?", (identifier,))
            else:
                old = db.execute("SELECT state FROM tickets WHERE id=?", (identifier,)).fetchone()
                if not old or old["state"] != "Cancelled":
                    raise ops.OperationError("Only unserved tickets on unpaid bills without active UPI checkout can be cancelled.")
            return "/orders" if form.get("origin") == "orders" else "/kitchen"
        if section == "correct-payment":
            payment = db.execute("SELECT * FROM payments WHERE visit_id=?", (identifier,)).fetchone()
            method, reason = form.get("method"), form.get("reason", "").strip()
            if not payment or method not in ops.METHODS or not 1 <= len(reason) <= 160:
                raise ops.OperationError("Select a payment method and enter a correction reason.")
            if payment["method"] != method:
                if payment["method"] != form.get("expected_method"):
                    raise ops.OperationError("The payment method changed. Reopen the invoice first.")
                db.execute("UPDATE payments SET method=? WHERE visit_id=?", (method, identifier))
                db.execute("""
                    INSERT INTO payment_corrections (visit_id,old_method,new_method,reason,created_at)
                    VALUES (?,?,?,?,?)
                """, (identifier, payment["method"], method, reason, ops.now()))
            return "/invoice/" + payment["receipt_token"]
        if section == "manage-menu":
            self.save_dish(db, identifier, form)
            return "/manage-menu"
        if section.startswith("staff-"):
            return self.save_staff(db, section, identifier, form)
        self.save_table(db, identifier, form)
        return "/"

    def save_table(self, db, identifier, form):
        name = form.get("name", "").strip()
        status = form.get("status")
        try:
            seats = int(form.get("seats", ""))
            if not 1 <= len(name) <= 50 or not 1 <= seats <= 100 or status not in STATES:
                raise ValueError
        except ValueError as error:
            raise ops.OperationError("Enter a table name, 1–100 seats and a valid status.") from error
        if identifier:
            previous = db.execute("SELECT status FROM dining_tables WHERE id=?", (identifier,)).fetchone()
            active = db.execute("""
                SELECT MAX(v.guests) FROM visits v LEFT JOIN payments p ON p.visit_id=v.id
                WHERE v.table_id=? AND p.visit_id IS NULL
                  AND EXISTS(SELECT 1 FROM tickets t WHERE t.visit_id=v.id AND t.state<>'Cancelled')
            """, (identifier,)).fetchone()[0]
            if active and (status != "Occupied" or seats < active):
                raise ops.OperationError("Keep the table occupied and seat its guests until the open bill is closed.")
            changed = db.execute("UPDATE dining_tables SET name=?,seats=?,status=? WHERE id=?",
                                 (name, seats, status, identifier)).rowcount
            if not changed:
                raise ops.OperationError("Table not found.")
            if status == "Cleaning" or (status == "Available" and previous["status"] != "Available"):
                db.execute("UPDATE waiter_calls SET resolved_at=? WHERE table_id=? AND resolved_at IS NULL",
                           (ops.now(), identifier))
        else:
            db.execute("INSERT INTO dining_tables VALUES (NULL,?,?,?,?)",
                       (name, seats, status, secrets.token_urlsafe(12)))


def main():
    parser = argparse.ArgumentParser(description="Run the TableFlow development server.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--lan-ip", help="Your computer’s local IPv4 address, for phone access.")
    args = parser.parse_args()
    if args.lan_ip:
        import ipaddress
        try:
            ipaddress.IPv4Address(args.lan_ip)
        except ValueError:
            parser.error("--lan-ip must be a valid IPv4 address")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    staff_key = os.getenv("TABLEFLOW_STAFF_KEY", "")
    if staff_key and not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", staff_key):
        parser.error("TABLEFLOW_STAFF_KEY must contain 24–128 letters, numbers, hyphens or underscores")
    os.environ["TABLEFLOW_PAYMENT_SANDBOX"] = "0"
    initialize()
    address = "0.0.0.0" if args.lan_ip else "127.0.0.1"
    with ThreadingHTTPServer((address, args.port), Handler) as server:
        server.staff_key = staff_key
        port = server.server_port
        server.menu_base = f"http://{args.lan_ip or '127.0.0.1'}:{port}"
        print(f"Staff dashboard: http://127.0.0.1:{port}", flush=True)
        print(f"Customer menu base: {server.menu_base}", flush=True)
        try:
            with online.payment_worker(database):
                server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
