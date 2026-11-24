import base64
import os
from datetime import datetime
import re
import secrets
from html import escape
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit

import operations as ops
import online
from qr import png as qr_png
from invoice_image import render_invoice
from staff import parse_day


def method_options(selected=None):
    return "".join(
        f'<option{" selected" if value == selected else ""}>{value}</option>'
        for value in ops.METHODS
    )


def rows_html(items):
    return "".join(f'<tr><td>{escape(row["name"])}</td><td>{row["quantity"]}</td>'
                   f'<td>{ops.money(row["price"] * row["quantity"])}</td></tr>' for row in items)


def item_table(items):
    return f'<table><thead><tr><th>Item</th><th>Qty</th><th>Amount</th></tr></thead><tbody>{rows_html(items)}</tbody></table>'


def review_link():
    value = os.getenv("TABLEFLOW_REVIEW_URL", "")
    try:
        url = urlsplit(value)
    except ValueError:
        return ""
    if url.scheme == "https" and url.hostname in (
        "g.page", "search.google.com", "maps.app.goo.gl", "www.google.com"
    ):
        return value
    return ""


DISH_FIELDS = (("allergens", "Allergens", 160), ("spice", "Spice level", 40),
               ("size", "Serving size", 50), ("offer", "Offer", 100),
               ("station", "Kitchen station", 40))
DETAILS_QUERY = """
    SELECT m.*, d.allergens,d.spice,d.size,d.offer,d.station
    FROM menu_items m LEFT JOIN dish_details d ON d.dish_id=m.id
"""


class Screens:
    def waiter_control(self, db, table, visit_token=""):
        if table["status"] == "Cleaning":
            return ""
        pending = db.execute("SELECT 1 FROM waiter_calls WHERE table_id=? AND resolved_at IS NULL",
                             (table["id"],)).fetchone()
        if pending:
            return '<p class="waiter-control" role="status">Waiter requested. A staff member will come to your table.</p>'
        return f"""<form class="waiter-control" method="post" action="/call-waiter/{table['token']}">
            <input type="hidden" name="visit" value="{escape(visit_token)}">
            <button class="secondary-button">Call waiter</button></form>"""

    def floor(self):
        with self.database() as db:
            tables = db.execute("""
                SELECT d.*, EXISTS(SELECT 1 FROM waiter_calls w WHERE w.table_id=d.id AND w.resolved_at IS NULL)
                AS needs_help FROM dining_tables d ORDER BY d.id
            """).fetchall()
        cards = "".join(f"""<article class="card panel floor-table{' needs-help' if table['needs_help'] else ''}">
          <h2>{escape(table['name'])}</h2><p>{table['seats']} seats</p>
          <span class="status {table['status'].lower()}">{table['status']}</span>
          {'<p><a href="/waiter">Waiter requested</a></p>' if table['needs_help'] else ''}</article>"""
          for table in tables)
        self.render("Floor", '<h1>Restaurant floor</h1><p>Refreshes every 8 seconds.</p>'
                    f'<div class="table-grid">{cards}</div>', auto_refresh=True)

    def waiter_calls(self):
        with self.database() as db:
            calls = db.execute("""
                SELECT d.id,d.name,w.called_at FROM waiter_calls w JOIN dining_tables d ON d.id=w.table_id
                WHERE w.resolved_at IS NULL ORDER BY w.called_at,d.id
            """).fetchall()
        rows = "".join(f"""<article class="card panel"><h2>{escape(row['name'])}</h2>
            <p>Requested {ops.local_time(row['called_at'])}</p>
            <form method="post" action="/resolve-waiter/{row['id']}">{self.csrf_input}
            <input type="hidden" name="called_at" value="{row['called_at']}">
            <button>Mark handled</button></form></article>""" for row in calls)
        self.render("Waiter calls", f'<h1>Waiter calls</h1><p>Refreshes every 8 seconds.</p>'
                    f'<div class="table-grid">{rows or "<p>No pending requests.</p>"}</div>', auto_refresh=True)

    def order_menu(self, table, selected, visit_token=""):
        with self.database() as db:
            visit = ops.find_visit(db, visit_token) if visit_token else None
            if visit_token and (not visit or visit["paid"] or not visit["has_items"] or visit["table_id"] != table["id"]):
                self.error_page(409, "This order is closed. Scan the table QR again to start a new order.",
                                f'/menu/{table["token"]}', guest=True)
                return
            dishes = db.execute(DETAILS_QUERY + " WHERE m.active=1 ORDER BY m.category,m.id").fetchall()
            ordering_open = table["status"] != "Cleaning"
            waiter = self.waiter_control(db, table, visit_token)
        categories = ["All", *dict.fromkeys(dish["category"] for dish in dishes)]
        if selected not in categories:
            selected = "All"
        visit_input = f'<input type="hidden" name="visit" value="{visit["token"]}">' if visit else ""
        guests = (f'<input type="hidden" name="guests" value="{visit["guests"]}">' if visit else
                  f'<label>Guests at this table<input name="guests" type="number" min="1" max="{table["seats"]}" value="1" required></label>')
        filters = "".join(
            f'<button type="button" data-menu-filter="{escape(category)}" aria-pressed="{str(category == selected).lower()}">{escape(category)}</button>'
            for category in categories
        )
        cards = []
        for dish in dishes:
            name = escape(dish["name"])
            category = escape(dish["category"])
            photo = dish["photo"]
            if photo.startswith("https://"):
                image = f'<img class="dish-photo" src="{escape(photo)}" alt="{name}" loading="lazy">'
            elif photo in ("paneer", "pizza", "latte", "brownie"):
                image = f'<div class="food {photo}" role="img" aria-label="{name}"></div>'
            else:
                image = '<div class="menu-no-photo">Photo unavailable</div>'
            details = "".join(f'<li><strong>{label}</strong> {escape(dish[key])}</li>'
                              for key, label, _ in DISH_FIELDS if key != "station" and dish[key])
            if details:
                details = f'<ul class="dish-details">{details}</ul>'
            controls = ""
            if ordering_open:
                controls = f"""<div class="menu-quantity">
                    <button type="button" data-step="-1" aria-label="Remove one {name}" hidden>−</button>
                    <label><span class="menu-sr-only">Quantity of {name}</span>
                    <input type="number" name="qty_{dish['id']}" min="0" max="20" step="1" value="0" required></label>
                    <button type="button" data-step="1" aria-label="Add one {name}" hidden>+</button>
                </div>"""
            cards.append(f"""<article class="menu-item" data-category="{category}"
                data-name="{name}" data-price="{dish['price']}">
                <div class="menu-item-image">{image}</div>
                <div class="menu-item-content"><span class="menu-category">{category}</span>
                <h2>{name}</h2><p>{escape(dish['description'])}</p>{details}
                <div class="menu-item-footer"><strong>{ops.money(dish['price'])}</strong>{controls}</div></div>
                </article>""")
        notice = ""
        if ops.whatsapp_config("SERVED_TEMPLATE"):
            notice = """
                <label>WhatsApp number (optional)<input type="tel" name="served_phone" maxlength="30" autocomplete="tel"></label>
                <label class="check"><input type="checkbox" name="served_notification" value="yes">
                Notify me on WhatsApp when this order is served.</label>"""
        submit = f"""
            <aside class="menu-cart" id="menu-cart" tabindex="-1" aria-labelledby="cart-title">
                <h2 id="cart-title">Your order</h2>
                <div data-cart-preview hidden><p data-cart-empty>Choose a dish to start your order.</p>
                <ul data-cart-items></ul><div class="menu-cart-total"><span>Subtotal</span>
                <strong data-cart-total aria-live="polite">₹0.00</strong></div></div>
                {guests}{notice}<button type="submit" data-send-order>Place order</button>
            </aside>
            <div class="menu-mobile-cart" data-mobile-cart hidden>
                <a href="#menu-cart"><span data-mobile-cart-count></span>
                <strong data-mobile-cart-total></strong><span>View order →</span></a>
            </div>"""
        if not ordering_open:
            submit = '<aside class="menu-cart"><h2>Ordering closed</h2><p>This table is being cleaned. Please check with staff.</p></aside>'
        back = f'<a href="/visit/{visit["token"]}">← View current order</a>' if visit else ""
        self.render("Menu", f"""
          <div class="customer-menu" data-customer-menu data-selected="{escape(selected)}">
            {back}<section class="menu-intro"><span class="menu-table">{escape(table["name"])}</span>
            <h1>Our menu</h1>{waiter}</section>
            <div class="menu-tools" data-menu-tools hidden>
              <nav aria-label="Menu categories">{filters}</nav>
              <label><span class="menu-sr-only">Search dishes</span><input type="search" placeholder="Search dishes" data-menu-search></label>
            </div>
            <form class="menu-layout" method="post" action="/order/{table['token']}">
              <input type="hidden" name="nonce" value="{secrets.token_urlsafe(24)}">{visit_input}
              <div><div class="menu-cards">{"".join(cards)}</div>
              <p data-menu-empty{" hidden" if cards else ""}>No dishes found.</p></div>
              {submit if cards else ""}
            </form>
          </div>
          <script src="/static/menu.js" defer></script>""", "guest menu-page")

    def visit_screen(self, visit):
        with self.database() as db:
            visit = ops.find_visit(db, visit["token"])
            items = ops.bill_items(db, visit["id"], include_tip=False)
            bill = ops.bill_items(db, visit["id"])
            preferences = db.execute("SELECT * FROM bill_preferences WHERE visit_id=?", (visit["id"],)).fetchone()
            tickets = db.execute("SELECT id,state FROM tickets WHERE visit_id=? ORDER BY id",
                                 (visit["id"],)).fetchall()
            table = db.execute("SELECT * FROM dining_tables WHERE id=?", (visit["table_id"],)).fetchone()
            ordering_open = table["status"] != "Cleaning"
            waiter = self.waiter_control(db, table, visit["token"])
            upi_request = db.execute("""
                SELECT u.*, r.reported_at FROM upi_requests u
                LEFT JOIN upi_payment_reports r ON r.reference=u.reference WHERE u.visit_id=?
            """, (visit["id"],)).fetchone()
            online_request = db.execute("SELECT * FROM online_checkouts WHERE visit_id=? AND state='pending'",
                                        (visit["id"],)).fetchone()
        token = visit["token"]
        status = "".join(f'<li>Order #{ticket["id"]} · {ticket["state"]}</li>' for ticket in tickets)
        lines = "".join(f'<li>{item["quantity"]} × {escape(item["name"])}</li>' for item in items)
        actions = f'<a href="/menu/{table["token"]}?visit={token}">Add more items</a> · ' if not visit["paid"] and visit["has_items"] and not upi_request and not online_request and ordering_open else ""
        awaiting = any(ticket["state"] == "Awaiting approval" for ticket in tickets)
        all_served = bool(items) and all(ticket["state"] in ("Served", "Cancelled") for ticket in tickets)
        payment = ""
        upi = ops.upi_config()
        if visit["paid"]:
            payment = f'<section class="card panel"><h2>Payment confirmed</h2><p><a href="/receipt/{token}">View receipt</a></p></section>'
        elif awaiting:
            payment = '<section class="card panel"><h2>Waiting for staff to accept your order</h2></section>'
        elif items and not all_served and not upi_request and not online_request:
            payment = '<section class="card panel"><h2>Pay at your table</h2><p>Pay bill will appear here once all dishes have been served.</p></section>'
        elif online_request or (items and not upi and online.credentials() and not upi_request):
            payment = self.online_payment(visit, bill, online_request, ordering_open)
        elif items and upi:
            total = ops.total_for(bill)
            if upi_request and upi_request["reported_at"]:
                payment = ('<section class="card panel"><h2>Waiting for payment verification</h2>'
                           '<p>You marked this bill as paid. Staff will check the payment.</p></section>')
            elif upi_request:
                params = urlencode({"pa": upi[0], "pn": upi[1], "tr": upi_request["reference"],
                                    "am": f'{upi_request["amount"] // 100}.{upi_request["amount"] % 100:02}',
                                    "cu": "INR"}, safe="@", quote_via=quote)
                address = f"upi://pay?{params}"
                qr = ""
                try:
                    encoded = base64.b64encode(qr_png(address)).decode()
                    qr = (f'<img class="upi-qr" width="208" height="208" src="data:image/png;base64,{encoded}" '
                          'alt="Scan this QR in a UPI app to pay the café">')
                except ValueError:
                    pass
                payment = f"""<section class="card panel"><h2>UPI payment</h2>
                    <p class="total">{ops.money(upi_request["amount"])}</p>{qr}
                    <p>Payee {escape(upi[1])} · {escape(upi[0])}</p>
                    <p>Payment reference <strong>{upi_request['reference']}</strong></p>
                    <p>Complete payment in your UPI app once. Staff will confirm receipt here.</p>
                    <a class="upi-button" href="{escape(address)}">Open UPI app</a>
                    <form method="post" action="/report-upi/{token}">
                    <input type="hidden" name="upi_reference" value="{upi_request['reference']}">
                    <button type="submit" class="secondary-button">I have paid</button></form></section>"""
            elif total and ordering_open:
                payment = f"""<section class="card panel"><h2>Pay at your table</h2>
                    <p class="total">{ops.money(total)}</p>
                    <form method="post" action="/start-upi/{token}">
                    <button type="submit" name="checkout" value="upi">Pay bill with UPI</button></form></section>"""
        if all_served and not visit["paid"] and not upi_request and not online_request and not (upi or online.credentials()):
            payment = '<section class="card panel"><h2>Pay at your table</h2><p>Use Call waiter to request your bill.</p></section>'
        bill_options = ""
        if all_served and not visit["paid"] and not awaiting and not upi_request and not online_request:
            tip = preferences["tip"] if preferences else 0
            split = preferences["split_count"] if preferences else 1
            total = ops.total_for(bill)
            share = ""
            if split > 1:
                each, extra = divmod(total, split)
                share = (f'<p>{split-extra} × {ops.money(each)}'
                         + (f' and {extra} × {ops.money(each+1)}' if extra else '')
                         + ' · split estimate. One payment settles this bill.</p>')
            bill_options = f"""<section class="card panel"><h2>Bill options</h2>{share}
                <form method="post" action="/bill-preferences/{token}">
                <div class="fields"><label>Tip (₹)<input name="tip" type="number" min="0" step="0.01" value="{tip/100:.2f}"></label>
                <label>Split estimate for how many people?<input name="split_count" type="number" min="1" max="20" value="{split}" required></label></div>
                <button>Update bill</button></form></section>"""
        wifi_name, wifi_password = os.getenv("TABLEFLOW_GUEST_WIFI_NAME"), os.getenv("TABLEFLOW_GUEST_WIFI_PASSWORD")
        wifi = ""
        if wifi_name and wifi_password:
            wifi = f'<details class="card panel"><summary>Guest Wi-Fi</summary><p>{escape(wifi_name)}</p><p>{escape(wifi_password)}</p></details>'
        review = review_link()
        review_html = f'<p><a href="{escape(review)}" rel="noopener noreferrer" target="_blank">Leave a Google review</a></p>' if visit["paid"] and review else ""
        self.render("Your order", f"""
            <section class="intro"><span class="table-label">{escape(visit["table_name"])}</span>
            <h1>Your order</h1>{waiter}</section>
            <section class="card panel"><h2>Order status</h2><ul>{status}</ul></section>
            <section class="card panel"><h2>Items ordered</h2><ul>{lines}</ul></section>
            {bill_options}{payment}{wifi}{review_html}<p>{actions}<a href="/visit/{token}">Refresh order status</a></p>""", "guest",
            auto_refresh=(bool(upi_request or online_request) and not visit["paid"]) or
                         any(ticket["state"] not in ("Served", "Cancelled") for ticket in tickets))

    def guest_receipt(self, visit_token):
        with self.database() as db:
            visit = ops.find_visit(db, visit_token)
            if not visit or not visit["paid"]:
                self.send_error(404)
                return
            payment = db.execute("SELECT method,total,paid_at,reward,loyalty_count FROM payments WHERE visit_id=?", (visit["id"],)).fetchone()
            items = ops.bill_items(db, visit["id"])
        reward = f'<p>{escape(ops.REWARD_ITEM)} · Loyalty reward earned</p>' if payment["reward"] else ""
        loyalty = f'<p>Loyalty visits {payment["loyalty_count"]}</p>' if payment["loyalty_count"] is not None else ""
        self.render("Receipt", f'<section class="card panel"><h1>Receipt</h1><p>{escape(visit["table_name"])} · '
                    f'{ops.local_time(payment["paid_at"])}</p>{item_table(items)}'
                    f'<p class="total">Paid {ops.money(payment["total"])} · {escape(payment["method"])}</p>'
                    f'{reward}{loyalty}<p><a href="/visit/{visit_token}">Back to your order</a></p></section>', "guest")

    def online_payment(self, visit, items, request, ordering_open):
        if request and request["checkout_url"]:
            address = request["checkout_url"]
            qr = ""
            try:
                encoded = base64.b64encode(qr_png(address)).decode()
                qr = f'<img width="208" height="208" src="data:image/png;base64,{encoded}" alt="Scan to open the café payment page">'
            except ValueError:
                pass
            return f"""<section class="card panel"><h2>Online payment</h2>
                <p class="total">{ops.money(request['amount'])}</p>{qr}
                <p>Scan on another device or open checkout here. Choose UPI or net banking if available.</p>
                <a class="upi-button" href="{escape(address)}" target="_blank" rel="noopener noreferrer">Open secure checkout</a>
                <p>This page updates after Razorpay confirms your payment.</p></section>"""
        if request:
            return '<section class="card panel"><h2>Preparing online checkout</h2><p>Keep this page open. Staff can check if the link stays unavailable.</p></section>'
        total = ops.total_for(items)
        return f"""<section class="card panel"><h2>Pay at your table</h2><p class="total">{ops.money(total)}</p>
            <form method="post" action="/start-online/{visit['token']}">
            <button name="checkout" value="online">Pay bill</button></form></section>""" if ordering_open and total else ""

    def order_inbox(self):
        with self.database() as db:
            tickets = db.execute("""
                SELECT t.*,v.table_name FROM tickets t JOIN visits v ON v.id=t.visit_id
                WHERE t.state='Awaiting approval' ORDER BY t.id
            """).fetchall()
            cards = []
            for ticket in tickets:
                items = db.execute("SELECT * FROM ticket_items WHERE ticket_id=? ORDER BY id", (ticket["id"],))
                lines = "".join(f'<li>{item["quantity"]} × {escape(item["name"])}</li>' for item in items)
                cards.append(f"""<article class="card panel"><h2>{escape(ticket['table_name'])} · #{ticket['id']}</h2>
                    <p>{ops.local_time(ticket['created_at'])}</p><ul>{lines}</ul>
                    <form method="post" action="/accept-order/{ticket['id']}">{self.csrf_input}
                    <button>Accept and send to kitchen</button></form>
                    <details><summary>Decline order</summary>
                    <form method="post" action="/cancel-ticket/{ticket['id']}">{self.csrf_input}
                    <input type="hidden" name="origin" value="orders">
                    <label>Reason<input name="reason" maxlength="160" required></label>
                    <button>Decline order</button></form></details></article>""")
        self.render("Incoming orders", '<h1>Incoming orders</h1><p>Accept orders to release them to the kitchen.</p>'
                    f'<div class="table-grid">{"".join(cards) or "<p>No orders awaiting approval.</p>"}</div>', auto_refresh=True)

    def kitchen(self, selected=""):
        with self.database() as db:
            tickets = db.execute("""
                SELECT t.*,v.table_name,p.visit_id IS NOT NULL AS paid,
                       COALESCE(k.released_at,t.created_at) AS kitchen_started
                FROM tickets t JOIN visits v ON v.id=t.visit_id
                LEFT JOIN payments p ON p.visit_id=v.id
                LEFT JOIN kitchen_releases k ON k.ticket_id=t.id
                WHERE t.state IN ('New','Preparing','Ready') ORDER BY t.id
            """).fetchall()
            cards = []
            stations = set()
            for ticket in tickets:
                items = db.execute("""
                    SELECT i.*, COALESCE(s.station,'Main kitchen') AS station
                    FROM ticket_items i LEFT JOIN ticket_item_stations s ON s.item_id=i.id
                    WHERE i.ticket_id=? ORDER BY i.id
                """, (ticket["id"],)).fetchall()
                labels = {row["station"] for row in items}
                stations.update(labels)
                if selected and selected not in labels:
                    continue
                lines = "".join(f'<li>{row["quantity"]} × {escape(row["name"])} · {escape(row["station"])}</li>' for row in items)
                elapsed = max(0, int((datetime.fromisoformat(ops.now()) -
                                      datetime.fromisoformat(ticket["kitchen_started"])).total_seconds()))
                following = ops.KITCHEN_STAGES[ops.KITCHEN_STAGES.index(ticket["state"]) + 1]
                cancel = ""
                if not ticket["paid"]:
                    cancel = f"""<details><summary>Cancel kitchen ticket</summary>
                        <form method="post" action="/cancel-ticket/{ticket['id']}">{self.csrf_input}
                        <label>Reason<input name="reason" maxlength="160" required></label>
                        <button>Cancel ticket</button></form></details>"""
                cards.append(f'<article class="card panel"><h2>{escape(ticket["table_name"])} · #{ticket["id"]}</h2>'
                             f'<p>{ticket["state"]} · <span data-kitchen-elapsed="{elapsed}">{elapsed // 60}m {elapsed % 60:02d}s</span> in kitchen</p><ul>{lines}</ul>'
                             f'<form method="post" action="/kitchen/{ticket["id"]}">{self.csrf_input}'
                             f'<input type="hidden" name="current" value="{ticket["state"]}">'
                             f'<button>Mark {following.lower()}</button></form>{cancel}</article>')
            notifications = db.execute("""
                SELECT ticket_id, state FROM served_notifications
                WHERE state <> 'Waiting for service' ORDER BY ticket_id DESC LIMIT 10
            """).fetchall()
        recent = ""
        if notifications:
            statuses = "".join(f'<li>Order #{row["ticket_id"]} · {escape(row["state"])}</li>'
                               for row in notifications)
            recent = f'<section class="card panel"><h2>Recent WhatsApp updates</h2><ul>{statuses}</ul></section>'
        station_options = '<a href="/kitchen">All stations</a>'
        station_options += "".join(f'<a href="/kitchen?{escape(urlencode({"station": name}))}">{escape(name)}</a>'
                                   for name in sorted(stations))
        self.render("Kitchen", f'<h1>Kitchen</h1><p>Refreshes every 8 seconds.</p><nav class="station-links" aria-label="Kitchen stations">{station_options}</nav>'
                    f'<div class="table-grid">{"".join(cards) or "<p>No orders waiting.</p>"}</div>{recent}', auto_refresh=True)

    def send_served_notification(self, ticket_id):
        config = ops.whatsapp_config("SERVED_TEMPLATE")
        if not config:
            return
        with self.database() as db:
            changed = db.execute("""
                UPDATE served_notifications SET state='Sending'
                WHERE ticket_id=? AND state='Pending'
            """, (ticket_id,)).rowcount
            row = (db.execute("SELECT phone FROM served_notifications WHERE ticket_id=?", (ticket_id,)).fetchone()
                   if changed else None)
        if not row:
            return
        provider_id = None
        try:
            provider_id = ops.whatsapp_send(config, row["phone"])
            state = "Accepted by WhatsApp"
        except HTTPError as error:
            state = "Failed" if 400 <= error.code < 500 else "Delivery uncertain"
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            state = "Delivery uncertain"
        with self.database() as db:
            db.execute("UPDATE served_notifications SET state=?, provider_id=? WHERE ticket_id=?",
                       (state, provider_id, ticket_id))

    def sales(self, query):
        today = datetime.now(ops.TIME_ZONE).date()
        day = parse_day(query.get("day", [today.isoformat()])[0])
        if day > today:
            raise ops.OperationError("Select today or an earlier sales date.")
        start, end = ops.daily_bounds(day)
        with self.database() as db:
            guests, visits = db.execute("SELECT COALESCE(SUM(guests),0),COUNT(*) FROM visits WHERE opened_at>=? AND opened_at<?",
                                       (start, end)).fetchone()
            orders = db.execute("SELECT COUNT(*) FROM tickets WHERE created_at>=? AND created_at<?", (start,end)).fetchone()[0]
            totals = {row["method"]: row for row in db.execute("""
                SELECT method,COUNT(*) AS transactions,SUM(total) AS amount FROM payments
                WHERE paid_at>=? AND paid_at<? GROUP BY method
            """, (start, end))}
            paid = db.execute("""
                SELECT p.*,v.table_name FROM payments p JOIN visits v ON v.id=p.visit_id
                WHERE paid_at>=? AND paid_at<? ORDER BY p.paid_at DESC,p.visit_id DESC
            """, (start, end)).fetchall()
            unpaid = db.execute("""
                SELECT v.*, o.state AS online_state FROM visits v LEFT JOIN payments p ON p.visit_id=v.id
                LEFT JOIN online_checkouts o ON o.visit_id=v.id
                WHERE p.visit_id IS NULL
                AND EXISTS(SELECT 1 FROM tickets t WHERE t.visit_id=v.id AND t.state<>'Cancelled')
                ORDER BY v.id
            """).fetchall()
        summary = [("Sales", ops.money(sum(row["amount"] for row in totals.values()))),
                   ("Guests", guests), ("Visits", visits), ("Kitchen tickets", orders)]
        for method in ops.METHODS:
            row = totals.get(method)
            summary.append((method, f'{ops.money(row["amount"] if row else 0)} · {row["transactions"] if row else 0} payments'))
        stats = "".join(f'<div class="card stat"><small>{label}</small><strong>{value}</strong></div>'
                        for label, value in summary)
        open_rows = "".join(f'<li><a href="/checkout/{row["id"]}">{escape(row["table_name"])} · Bill #{row["id"]}</a>'
                            f' · {"Online payment pending" if row["online_state"] == "pending" else row["requested_method"] or "Awaiting checkout"}</li>' for row in unpaid)
        history = "".join(f'<tr><td><a href="/invoice/{row["receipt_token"]}">TF-{row["visit_id"]:06}</a></td>'
                          f'<td>{escape(row["table_name"])}</td><td>{row["method"]}</td><td>{ops.money(row["total"])}</td></tr>'
                          for row in paid)
        self.render("Sales", f'<h1>Sales by date</h1><p>Business day in India Standard Time · <a href="/sales/monthly">Monthly sales reports</a></p>'
                    f'<form class="card panel" action="/sales" method="get"><label>Date<input type="date" name="day" value="{day}" max="{today}" required></label><button>View sales</button></form><div class="stats">{stats}</div>'
                    f'<section class="card panel"><h2>Open bills</h2><ul>{open_rows or "<li>No open bills</li>"}</ul></section>'
                    f'<section class="card panel"><h2>Transactions for selected date</h2><table><thead><tr><th>Invoice</th><th>Table</th>'
                    f'<th>Method</th><th>Amount</th></tr></thead><tbody>{history}</tbody></table></section>',
                    auto_refresh=any(row["online_state"] == "pending" for row in unpaid))

    def checkout(self, visit_id):
        with self.database() as db:
            row = db.execute("SELECT token FROM visits WHERE id=?", (visit_id,)).fetchone()
            if not row:
                self.send_error(404)
                return
            visit = ops.find_visit(db, row["token"])
            if visit["paid"]:
                self.redirect("/invoice/" + visit["receipt_token"])
                return
            items = ops.bill_items(db, visit_id)
            if not items:
                self.error_page(400, "All tickets for this bill were cancelled.", "/sales")
                return
            table = db.execute("SELECT token FROM dining_tables WHERE id=?", (visit["table_id"],)).fetchone()
            upi_request = db.execute("""
                SELECT u.*, r.reported_at FROM upi_requests u
                LEFT JOIN upi_payment_reports r ON r.reference=u.reference WHERE u.visit_id=?
            """, (visit_id,)).fetchone()
            online_request = db.execute("SELECT 1 FROM online_checkouts WHERE visit_id=? AND state='pending'",
                                        (visit_id,)).fetchone()
            count = ops.loyalty_count(db, visit["phone"]) if visit["phone"] else 0
        if online_request:
            self.render("Checkout", f'<h1>Checkout · {escape(visit["table_name"])}</h1>'
                        '<p>Online payment pending. This screen refreshes until Razorpay confirms payment '
                        'or the checkout expires.</p><p><a href="/sales">Back to sales</a></p>', auto_refresh=True)
            return
        reward = f'<p>Payment unlocks a free {ops.REWARD_ITEM} for this visit.</p>' if count % ops.REWARD_AT == ops.REWARD_AT - 1 else ""
        checked = " checked" if visit["whatsapp_opt_in"] else ""
        shared = ""
        if self.server.menu_base and not upi_request:
            link = f'{self.server.menu_base}/menu/{table["token"]}?visit={visit["token"]}'
            shared = f'<p><a href="{escape(link)}">Customer add-items link</a> · Share only with guests at this table.</p>'
        pending = ""
        if upi_request:
            state = "Customer reports payment · verify receipt" if upi_request["reported_at"] else "UPI requested"
            pending = f"""<p>{state} · {ops.money(upi_request['amount'])} · Reference <strong>{upi_request['reference']}</strong></p>
                <details><summary>Reopen order for changes</summary>
                <p>Use this only after checking the merchant UPI app and confirming the customer has abandoned this UPI payment.</p>
                <form method="post" action="/reopen-upi/{visit_id}">{self.csrf_input}
                <input type="hidden" name="upi_reference" value="{upi_request['reference']}">
                <button>Reopen order</button></form></details>"""
        selected_method = "UPI" if upi_request else visit["requested_method"]
        self.render("Checkout", f"""
            <section class="intro"><h1>Checkout</h1><p>{escape(visit["table_name"])} · Bill #{visit_id}</p></section>
            <section class="card panel">{item_table(items)}<p class="total">{ops.money(ops.total_for(items))}</p>{reward}
            <p>Confirm once the payment is received at the counter or verified on your terminal or UPI app.</p>
            {shared}{pending}<form method="post" action="/checkout/{visit_id}">{self.csrf_input}
            <input type="hidden" name="revision" value="{ops.bill_revision(items)}">
            <div class="fields"><label>Payment received via<select name="method">{method_options(selected_method)}</select></label>
            <label>Transaction reference (optional)<input name="reference" maxlength="80"></label></div>
            <label>Customer mobile for loyalty (optional)<input type="tel" name="phone" maxlength="30" value="{escape(visit["phone"])}" placeholder="+91"></label>
            <label class="check"><input type="checkbox" name="whatsapp" value="yes"{checked}> Customer agreed to receive their invoice, review link and any reward on WhatsApp.</label>
            <button>Confirm payment and issue invoice</button></form></section>""")

    def invoice_data(self, token):
        with self.database() as db:
            row = db.execute("""
                SELECT p.*,v.table_name,v.phone,v.whatsapp_opt_in,o.state AS message_state
                FROM payments p JOIN visits v ON v.id=p.visit_id
                LEFT JOIN whatsapp_outbox o ON o.visit_id=p.visit_id WHERE p.receipt_token=?
            """, (token,)).fetchone()
            return (row, ops.bill_items(db, row["visit_id"])) if row else (None, [])

    def invoice_photo(self, token):
        row, items = self.invoice_data(token)
        if not row:
            self.send_error(404)
            return
        try:
            photo = render_invoice(row, items)
        except OSError as error:
            self.error_page(503, str(error), f"/invoice/{token}")
            return
        self.send_content(200, photo, "image/png", filename=f'invoice-TF-{row["visit_id"]:06}.png')

    def invoice(self, token):
        row, items = self.invoice_data(token)
        if not row:
            self.send_error(404)
            return
        review = review_link()
        review_html = f'<p><a href="{escape(review)}" rel="noreferrer">Leave a Google review</a></p>' if review else ""
        reward = f'<p><strong>Loyalty reward</strong> · Your free {ops.REWARD_ITEM} has been sent to the kitchen.</p>' if row["reward"] else ""
        loyalty = f'<p>Loyalty card · {row["loyalty_count"] % ops.REWARD_AT} of {ops.REWARD_AT} stamps</p>' if row["loyalty_count"] is not None else ""
        whatsapp = ""
        if row["whatsapp_opt_in"]:
            link = f'https://wa.me/{row["phone"].lstrip("+")}'
            retry = ""
            if ops.whatsapp_config() and row["message_state"] in ("Pending", "Failed"):
                retry = (f'<form method="post" action="/whatsapp/{token}">{self.csrf_input}'
                         '<button>Send via WhatsApp Business</button></form>')
            whatsapp = f'<div class="card panel"><h2>WhatsApp</h2><p>{row["message_state"]}</p><a href="{link}" rel="noreferrer">Open WhatsApp chat to attach the PNG</a>{retry}</div>'
        with self.database() as db:
            corrections = db.execute("""
                SELECT * FROM payment_corrections WHERE visit_id=? ORDER BY id DESC
            """, (row["visit_id"],)).fetchall()
        history = "".join(f'<li>{change["old_method"]} → {change["new_method"]} · '
                          f'{escape(change["reason"])} · {ops.local_time(change["created_at"])}</li>'
                          for change in corrections)
        correction = f"""
          <details class="card panel print-note"><summary>Correct recorded payment method</summary>
          <form method="post" action="/correct-payment/{row["visit_id"]}">{self.csrf_input}
          <input type="hidden" name="expected_method" value="{row["method"]}">
          <label>Payment method<select name="method">{method_options(row["method"])}</select></label>
          <label>Reason<input name="reason" maxlength="160" required></label>
          <button>Save correction</button></form><ul>{history}</ul></details>"""
        self.render("Invoice", f"""
            <section class="card panel invoice"><h1>Invoice TF-{row["visit_id"]:06}</h1>
            <p>{escape(row["table_name"])} · {ops.local_time(row["paid_at"])}</p>
            {item_table(items)}<p class="total">Paid {ops.money(row["total"])} · {row["method"]}</p>
            {reward}{loyalty}<p>Thank you for visiting!</p>{review_html}
            <p class="print-note"><a href="/invoice/{token}/photo">Download invoice photo (PNG)</a> · Use Print to save a PDF.</p></section>{correction}{whatsapp}""")

    def process_whatsapp(self, token):
        config = ops.whatsapp_config()
        row, items = self.invoice_data(token)
        if not config or not row or not row["whatsapp_opt_in"]:
            return
        with self.database() as db:
            changed = db.execute("""
                UPDATE whatsapp_outbox SET state='Sending' WHERE visit_id=?
                AND state IN ('Pending','Failed')
            """, (row["visit_id"],)).rowcount
        if not changed:
            return
        provider_id = None
        media_id = None
        try:
            photo = render_invoice(row, items)
            if len(photo) > 5_000_000:
                raise ValueError("Invoice exceeds the WhatsApp image size limit.")
            media_id = ops.upload_invoice_photo(config, photo, f'invoice-TF-{row["visit_id"]:06}.png')
            provider_id = ops.whatsapp_send(config, row["phone"], media_id)
            state = "Accepted by WhatsApp"
        except HTTPError as error:
            state = "Failed" if not media_id or 400 <= error.code < 500 else "Delivery uncertain"
        except (OSError, ValueError, TypeError, KeyError, IndexError):
            state = "Delivery uncertain" if media_id else "Failed"
        with self.database() as db:
            db.execute("UPDATE whatsapp_outbox SET state=?,provider_id=? WHERE visit_id=?",
                       (state, provider_id, row["visit_id"]))

    def menu_admin(self):
        with self.database() as db:
            dishes = db.execute(DETAILS_QUERY + " ORDER BY m.id").fetchall()
        entries = [dict(id="", name="", category="", description="", price=0, photo="", active=1,
                        **{key: "" for key, _, _ in DISH_FIELDS}), *dishes]
        cards = []
        for row in entries:
            identifier = f'/{row["id"]}' if row["id"] else ""
            checked = " checked" if row["active"] else ""
            title = escape(row["name"]) if row["id"] else "Add a dish"
            photo = row["photo"] if row["photo"].startswith("https://") else ""
            price = f'{row["price"] // 100}.{row["price"] % 100:02d}' if row["price"] else ""
            details = "".join(f'<label>{label}<input name="{key}" value="{escape(row[key] or "")}" maxlength="{limit}"></label>'
                              for key, label, limit in DISH_FIELDS)
            cards.append(f"""<details class="card panel"><summary>{title}</summary>
                <form method="post" action="/manage-menu{identifier}">{self.csrf_input}
                <div class="fields"><label>Dish name<input name="name" value="{escape(row["name"])}" maxlength="80" required></label>
                <label>Category<input name="category" value="{escape(row["category"])}" maxlength="35" required></label></div>
                <label>Description<input name="description" value="{escape(row["description"])}" maxlength="280" required></label>
                <label>Price (INR)<input name="price" type="number" min="0.01" max="99999" step="0.01" value="{price}" required></label>
                <label>Photo URL (HTTPS)<input name="photo" type="url" value="{escape(photo)}" maxlength="500"></label>
                <div class="fields">{details}</div>
                <label class="check"><input type="checkbox" name="active" value="yes"{checked}> Available to order</label>
                <button>Save dish</button></form></details>""")
        self.render("Manage menu", f'<h1>Manage menu</h1>{"".join(cards)}')

    def save_dish(self, db, identifier, values):
        name, category, description, photo = (values.get(key, "").strip() for key in ("name", "category", "description", "photo"))
        if not all(0 < len(value) <= limit for value, limit in ((name,80),(category,35),(description,280))):
            raise ops.OperationError("Enter a dish name, category and description.")
        price = values.get("price", "")
        if not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,2})?", price):
            raise ops.OperationError("Enter a valid price in INR.")
        rupees, _, fraction = price.partition(".")
        paise = int(rupees) * 100 + int(fraction.ljust(2, "0"))
        if not 0 < paise <= 9999900:
            raise ops.OperationError("Enter a price from ₹0.01 to ₹99,999.")
        if photo:
            try:
                url = urlsplit(photo)
                if len(photo) > 500 or url.scheme != "https" or not url.hostname:
                    raise ValueError
            except ValueError as error:
                raise ops.OperationError("Use an HTTPS image URL.") from error
        details = [values.get(key, "").strip() for key, _, _ in DISH_FIELDS]
        if any(len(value) > field[2] for value, field in zip(details, DISH_FIELDS)):
            raise ops.OperationError("One or more dish detail fields are too long.")
        details[-1] = details[-1] or "Main kitchen"
        active = values.get("active") == "yes"
        if identifier:
            previous = db.execute("SELECT photo FROM menu_items WHERE id=?", (identifier,)).fetchone()
            if not previous:
                raise ops.OperationError("Dish not found.")
            if not photo and previous["photo"] in ("paneer", "pizza", "latte", "brownie"):
                photo = previous["photo"]
            db.execute("UPDATE menu_items SET name=?,category=?,description=?,price=?,photo=?,active=? WHERE id=?",
                       (name,category,description,paise,photo,active,identifier))
        else:
            identifier = db.execute("INSERT INTO menu_items (name,category,description,price,photo,active) VALUES (?,?,?,?,?,?)",
                                    (name,category,description,paise,photo,active)).lastrowid
        if any(key in values for key, _, _ in DISH_FIELDS):
            db.execute("""
                INSERT INTO dish_details VALUES (?,?,?,?,?,?)
                ON CONFLICT(dish_id) DO UPDATE SET allergens=excluded.allergens,spice=excluded.spice,
                    size=excluded.size,offer=excluded.offer,station=excluded.station
            """, (identifier, *details))
