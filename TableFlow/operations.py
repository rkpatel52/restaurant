import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

METHODS = ("Cash", "Card", "UPI", "Net banking", "Wallet", "Online")
KITCHEN_STAGES = ("New", "Preparing", "Ready", "Served")
REWARD_ITEM = "Chocolate Brownie"
REWARD_AT = 10
TIME_ZONE = timezone(timedelta(hours=5, minutes=30), "IST")

SCHEMA = """
CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
    description TEXT NOT NULL, price INTEGER NOT NULL CHECK(price > 0),
    photo TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS dish_details (
    dish_id INTEGER PRIMARY KEY REFERENCES menu_items(id),
    allergens TEXT NOT NULL, spice TEXT NOT NULL, size TEXT NOT NULL,
    offer TEXT NOT NULL, station TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_item_stations (
    item_id INTEGER PRIMARY KEY REFERENCES ticket_items(id),
    station TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kitchen_releases (
    ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id), released_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY, table_id INTEGER NOT NULL REFERENCES dining_tables(id),
    table_name TEXT NOT NULL, token TEXT NOT NULL UNIQUE,
    guests INTEGER NOT NULL CHECK(guests > 0), opened_at TEXT NOT NULL,
    requested_method TEXT, phone TEXT NOT NULL DEFAULT '',
    whatsapp_opt_in INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bill_preferences (
    visit_id INTEGER PRIMARY KEY REFERENCES visits(id),
    tip INTEGER NOT NULL CHECK(tip >= 0),
    split_count INTEGER NOT NULL CHECK(split_count BETWEEN 1 AND 20)
);
CREATE TABLE IF NOT EXISTS payments (
    visit_id INTEGER PRIMARY KEY REFERENCES visits(id), method TEXT NOT NULL,
    total INTEGER NOT NULL, paid_at TEXT NOT NULL,
    receipt_token TEXT NOT NULL UNIQUE, reward INTEGER NOT NULL DEFAULT 0,
    loyalty_count INTEGER, reference TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL REFERENCES visits(id),
    request_token TEXT NOT NULL UNIQUE, state TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_items (
    id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
    price INTEGER NOT NULL CHECK(price >= 0)
);
CREATE TABLE IF NOT EXISTS loyalty (
    phone TEXT PRIMARY KEY, paid_visits INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS whatsapp_outbox (
    visit_id INTEGER PRIMARY KEY REFERENCES payments(visit_id),
    state TEXT NOT NULL DEFAULT 'Pending', provider_id TEXT
);
CREATE TABLE IF NOT EXISTS served_notifications (
    ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id),
    phone TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'Waiting for service',
    provider_id TEXT
);
CREATE TABLE IF NOT EXISTS waiter_calls (
    table_id INTEGER PRIMARY KEY REFERENCES dining_tables(id),
    called_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS upi_requests (
    visit_id INTEGER PRIMARY KEY REFERENCES visits(id),
    reference TEXT NOT NULL UNIQUE,
    amount INTEGER NOT NULL CHECK(amount > 0)
);
CREATE TABLE IF NOT EXISTS upi_payment_reports (
    reference TEXT PRIMARY KEY REFERENCES upi_requests(reference) ON DELETE CASCADE,
    reported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_corrections (
    id INTEGER PRIMARY KEY, visit_id INTEGER NOT NULL REFERENCES payments(visit_id),
    old_method TEXT NOT NULL, new_method TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_cancellations (
    ticket_id INTEGER PRIMARY KEY REFERENCES tickets(id), reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS visits_by_table ON visits(table_id);
CREATE INDEX IF NOT EXISTS tickets_by_visit ON tickets(visit_id);
CREATE INDEX IF NOT EXISTS items_by_ticket ON ticket_items(ticket_id);
"""


class OperationError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def daily_bounds(day=None):
    start = datetime.combine(day or datetime.now(TIME_ZONE).date(), datetime.min.time(), TIME_ZONE)
    return tuple(moment.astimezone(timezone.utc).isoformat(timespec="seconds")
                 for moment in (start, start + timedelta(days=1)))


def local_time(value):
    return datetime.fromisoformat(value).astimezone(TIME_ZONE).strftime("%d %b %Y, %I:%M %p IST")


def money(amount):
    rupees, paise = divmod(amount, 100)
    return f"₹{rupees:,}.{paise:02d}"


def phone_number(value):
    cleaned = re.sub(r"[\s()-]", "", value)
    if not cleaned:
        return ""
    if re.fullmatch(r"[6-9]\d{9}", cleaned, flags=re.ASCII):
        return "+91" + cleaned
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", cleaned):
        raise OperationError("Enter a 10-digit Indian mobile number or use its international country code.")
    return cleaned


def find_visit(db, token):
    return db.execute("""
        SELECT v.*, p.visit_id IS NOT NULL AS paid, p.receipt_token, EXISTS(SELECT 1 FROM tickets t WHERE t.visit_id=v.id AND t.state<>'Cancelled') AS has_items
        FROM visits v LEFT JOIN payments p ON p.visit_id=v.id WHERE v.token=?
    """, (token,)).fetchone()


def bill_items(db, visit_id, include_tip=True):
    rows = db.execute("""
        SELECT i.* FROM ticket_items i JOIN tickets t ON t.id=i.ticket_id
        WHERE t.visit_id=? AND t.state<>'Cancelled' ORDER BY t.id, i.id
    """, (visit_id,)).fetchall()
    if rows and include_tip:
        preference = db.execute("SELECT tip FROM bill_preferences WHERE visit_id=?", (visit_id,)).fetchone()
        if preference and preference["tip"]:
            rows.append({"id": f'tip:{preference["tip"]}', "name": "Tip", "quantity": 1, "price": preference["tip"]})
    return rows


def bill_revision(items):
    return hashlib.sha256(",".join(str(item["id"]) for item in items).encode()).hexdigest()


def total_for(items):
    return sum(row["price"] * row["quantity"] for row in items)


def add_ticket(db, visit_id, nonce, items, state="New"):
    ticket_id = db.execute(
        "INSERT INTO tickets (visit_id,request_token,state,created_at) VALUES (?,?,?,?)",
        (visit_id, nonce, state, now()),
    ).lastrowid
    db.executemany("INSERT INTO ticket_items (ticket_id,name,quantity,price) VALUES (?,?,?,?)",
                   [(ticket_id, name, quantity, price) for name, quantity, price in items])
    return ticket_id


def place_order(db, table, values):
    nonce = values.get("nonce", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", nonce):
        raise OperationError("Refresh the menu and try again.")
    existing = db.execute("""
        SELECT v.token FROM tickets t JOIN visits v ON v.id=t.visit_id
        WHERE t.request_token=? AND v.table_id=?
    """, (nonce, table["id"])).fetchone()
    if existing:
        return existing["token"]
    if table["status"] == "Cleaning":
        raise OperationError("This table is being cleaned. Please ask a staff member for help.")

    dishes = db.execute("""
        SELECT m.*, COALESCE(d.station,'Main kitchen') AS station
        FROM menu_items m LEFT JOIN dish_details d ON d.dish_id=m.id WHERE m.active=1 ORDER BY m.id
    """).fetchall()
    items, stations = [], []
    try:
        guests = int(values.get("guests", "1"))
        if not 1 <= guests <= table["seats"]:
            raise ValueError
        for dish in dishes:
            quantity = int(values.get(f"qty_{dish['id']}", "0"))
            if not 0 <= quantity <= 20:
                raise ValueError
            if quantity:
                items.append((dish["name"], quantity, dish["price"]))
                stations.append(dish["station"])
    except ValueError as error:
        raise OperationError("Enter a valid guest count and up to 20 of each item.") from error
    if not items:
        raise OperationError("Choose at least one available menu item.")

    active = db.execute("""
        SELECT v.id, v.token FROM visits v LEFT JOIN payments p ON p.visit_id=v.id
        WHERE v.table_id=? AND p.visit_id IS NULL
          AND EXISTS(SELECT 1 FROM tickets t WHERE t.visit_id=v.id AND t.state<>'Cancelled')
        ORDER BY v.id DESC LIMIT 1
    """, (table["id"],)).fetchone()
    provided = values.get("visit", "")
    if provided and (not active or provided != active["token"]):
        raise OperationError("This order is closed. Scan the table QR again to start a new order.")
    if active:
        if not provided.isascii() or not secrets.compare_digest(provided, active["token"]):
            raise OperationError("A bill is already open. Open it on the phone that placed the first order.")
        visit_id, token = active["id"], active["token"]
        if db.execute("SELECT 1 FROM online_checkouts WHERE visit_id=? AND state='pending'", (visit_id,)).fetchone():
            raise OperationError("Online checkout is pending. Wait for confirmation or for the link to expire.")
        if db.execute("SELECT 1 FROM upi_requests WHERE visit_id=?", (visit_id,)).fetchone():
            raise OperationError("UPI checkout has started. Ask staff before changing your order.")
        db.execute("UPDATE visits SET requested_method=NULL WHERE id=?", (visit_id,))
    else:
        latest = db.execute("""
            SELECT p.visit_id AS paid_id FROM visits v LEFT JOIN payments p ON p.visit_id=v.id
            WHERE v.table_id=? ORDER BY v.id DESC LIMIT 1
        """, (table["id"],)).fetchone()
        if table["status"] == "Occupied" and latest and latest["paid_id"] is not None:
            raise OperationError("This table is still occupied after checkout. Please ask staff to release it.")
        token = secrets.token_urlsafe(24)
        visit_id = db.execute("""
            INSERT INTO visits (table_id,table_name,token,guests,opened_at) VALUES (?,?,?,?,?)
        """, (table["id"], table["name"], token, guests, now())).lastrowid
        db.execute("UPDATE dining_tables SET status='Occupied' WHERE id=?", (table["id"],))
    ticket_id = add_ticket(db, visit_id, nonce, items, state="Awaiting approval")
    item_ids = db.execute("SELECT id FROM ticket_items WHERE ticket_id=? ORDER BY id", (ticket_id,))
    db.executemany("INSERT INTO ticket_item_stations VALUES (?,?)",
                   [(row["id"], station) for row, station in zip(item_ids, stations)])
    if values.get("served_notification") == "yes" and whatsapp_config("SERVED_TEMPLATE"):
        phone = phone_number(values.get("served_phone", ""))
        if not phone:
            raise OperationError("Enter your WhatsApp number to receive the served notification.")
        db.execute("INSERT INTO served_notifications(ticket_id, phone) VALUES (?,?)", (ticket_id, phone))
    return token


def require_approved_orders(db, visit_id):
    if db.execute("SELECT 1 FROM tickets WHERE visit_id=? AND state='Awaiting approval'", (visit_id,)).fetchone():
        raise OperationError("Wait for staff to accept your new orders before checkout.")


def require_served_orders(db, visit_id):
    if db.execute("""
        SELECT 1 FROM tickets WHERE visit_id=? AND state NOT IN ('Served','Cancelled')
    """, (visit_id,)).fetchone():
        raise OperationError("Pay at the table once all dishes have been marked served.")


def upi_config():
    address = os.getenv("TABLEFLOW_UPI_ID", "").strip()
    name = os.getenv("TABLEFLOW_UPI_NAME", "").strip()
    if re.fullmatch(r"[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+", address) and 1 <= len(name) <= 80:
        return address, name
    return None


def request_upi(db, visit):
    if not upi_config() or not visit or visit["paid"] or not visit["has_items"]:
        raise OperationError("UPI is unavailable for this order.")
    if db.execute("SELECT 1 FROM online_checkouts WHERE visit_id=? AND state='pending'", (visit["id"],)).fetchone():
        raise OperationError("An online checkout is already pending.")
    require_served_orders(db, visit["id"])
    total = total_for(bill_items(db, visit["id"]))
    if total <= 0:
        raise OperationError("This order has no amount to pay.")
    db.execute("""
        INSERT INTO upi_requests VALUES (?,?,?) ON CONFLICT(visit_id) DO NOTHING
    """, (visit["id"], "TF" + secrets.token_hex(8), total))


def loyalty_count(db, phone):
    row = db.execute("SELECT paid_visits FROM loyalty WHERE phone=?", (phone,)).fetchone()
    return row["paid_visits"] if row else 0


def confirm_payment(db, visit, values):
    if visit["paid"]:
        return visit["receipt_token"]
    require_approved_orders(db, visit["id"])
    method = values.get("method")
    if method not in METHODS:
        raise OperationError("Choose Cash, Card or UPI.")
    reference = values.get("reference", "").strip()
    if len(reference) > 80:
        raise OperationError("Use an optional transaction reference of 80 characters or fewer.")
    items = bill_items(db, visit["id"])
    if not items or bill_revision(items) != values.get("revision"):
        raise OperationError("This bill changed. Reopen checkout to review its latest total.")

    phone = phone_number(values.get("phone", visit["phone"]))
    opted_in = (values.get("whatsapp") == "yes") if "phone" in values else bool(visit["whatsapp_opt_in"])
    if opted_in and not phone:
        raise OperationError("Add the customer’s mobile number before enabling WhatsApp.")
    db.execute("UPDATE visits SET phone=?, whatsapp_opt_in=? WHERE id=?",
               (phone, opted_in, visit["id"]))

    count = None
    if phone:
        count = loyalty_count(db, phone) + 1
        db.execute("""
            INSERT INTO loyalty (phone, paid_visits) VALUES (?,?)
            ON CONFLICT(phone) DO UPDATE SET paid_visits=excluded.paid_visits
        """, (phone, count))
    reward = count is not None and count % REWARD_AT == 0
    if reward:
        add_ticket(db, visit["id"], secrets.token_urlsafe(24), [(REWARD_ITEM + " (loyalty reward)", 1, 0)])
    receipt = secrets.token_urlsafe(24)
    db.execute("""
        INSERT INTO payments (visit_id, method, total, paid_at, receipt_token, reward, loyalty_count, reference)
        VALUES (?,?,?,?,?,?,?,?)
    """, (visit["id"], method, total_for(items), now(), receipt, reward, count, reference))
    if opted_in:
        db.execute("INSERT INTO whatsapp_outbox (visit_id) VALUES (?)", (visit["id"],))
    return receipt


def whatsapp_config(template_key="IMAGE_TEMPLATE"):
    settings = tuple(os.getenv("TABLEFLOW_WA_" + key, "").strip()
                     for key in ("VERSION", "PHONE_ID", "TOKEN", template_key, "LANGUAGE"))
    version, phone_id, token, template, language = settings
    if (re.fullmatch(r"v[0-9]+\.[0-9]+", version)
            and phone_id.isascii() and phone_id.isdecimal() and token
            and re.fullmatch(r"[a-z0-9_]+", template)
            and re.fullmatch(r"[A-Za-z_]+", language)):
        return settings
    return None


def upload_invoice_photo(config, photo, filename):
    version, phone_id, token, _, _ = config
    boundary = secrets.token_hex(16)
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="messaging_product"\r\n\r\nwhatsapp\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode(),
        photo,
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\nimage/png\r\n--{boundary}--\r\n'.encode(),
    ]
    request = Request(f"https://graph.facebook.com/{version}/{phone_id}/media",
                      data=b"".join(parts), method="POST",
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urlopen(request, timeout=15) as response:
        return json.load(response)["id"]


def whatsapp_send(config, phone, media_id=None):
    version, phone_id, access_token, template, language = config
    template_data = {"name": template, "language": {"code": language}}
    if media_id is not None:
        template_data["components"] = [{
            "type": "header",
            "parameters": [{"type": "image", "image": {"id": media_id}}],
        }]
    payload = {
        "messaging_product": "whatsapp",
        "to": phone.lstrip("+"),
        "type": "template",
        "template": template_data,
    }
    request = Request(f"https://graph.facebook.com/{version}/{phone_id}/messages",
                      data=json.dumps(payload).encode(), method="POST",
                      headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
    with urlopen(request, timeout=8) as response:
        return json.load(response)["messages"][0]["id"]
