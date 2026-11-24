"""Razorpay-hosted checkout and server-side payment reconciliation."""

import base64
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from threading import Event, Thread
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import operations as ops

SCHEMA = """
CREATE TABLE IF NOT EXISTS online_checkouts (
    visit_id INTEGER PRIMARY KEY REFERENCES visits(id),
    reference TEXT NOT NULL UNIQUE,
    amount INTEGER NOT NULL,
    revision TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    account_key TEXT NOT NULL,
    provider_id TEXT,
    checkout_url TEXT,
    state TEXT NOT NULL DEFAULT 'pending'
);
"""
METHODS = {"upi": "UPI", "card": "Card", "netbanking": "Net banking", "wallet": "Wallet"}


def credentials():
    key = os.getenv("TABLEFLOW_RAZORPAY_KEY_ID", "").strip()
    secret = os.getenv("TABLEFLOW_RAZORPAY_KEY_SECRET", "").strip()
    mode = "test" if os.getenv("TABLEFLOW_PAYMENT_SANDBOX") == "1" else "live"
    return (key, secret) if re.fullmatch(rf"rzp_{mode}_[A-Za-z0-9]+", key) and secret else None


def reserve(db, visit):
    config = credentials()
    if ops.upi_config() or not config or not visit or visit["paid"] or not visit["has_items"]:
        raise ops.OperationError("Online checkout is unavailable for this order.")
    if db.execute("SELECT 1 FROM upi_requests WHERE visit_id=?", (visit["id"],)).fetchone():
        raise ops.OperationError("A UPI request already exists. Ask staff before switching payment methods.")
    ops.require_served_orders(db, visit["id"])
    items = ops.bill_items(db, visit["id"])
    amount = ops.total_for(items)
    if amount <= 0:
        raise ops.OperationError("This bill has no amount to pay.")
    db.execute("""
        INSERT INTO online_checkouts(visit_id,reference,amount,revision,expires_at,account_key)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(visit_id) DO UPDATE SET reference=excluded.reference,amount=excluded.amount,
          revision=excluded.revision,expires_at=excluded.expires_at,account_key=excluded.account_key,
          provider_id=NULL,checkout_url=NULL,state='pending'
        WHERE online_checkouts.state='closed'
    """, (visit["id"], "TF" + secrets.token_hex(12), amount, ops.bill_revision(items),
          int(time.time()) + 3600, config[0]))


def api(config, path, data=None):
    auth = base64.b64encode(":".join(config).encode()).decode()
    request = Request("https://api.razorpay.com/v1/" + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def link_details(config, row):
    if row["provider_id"]:
        return api(config, "payment_links/" + row["provider_id"])
    result = api(config, "payment_links?" + urlencode({"reference_id": row["reference"], "count": 1}))
    matches = result.get("payment_links")
    if matches is None:
        matches = result.get("items")
    if not isinstance(matches, list):
        raise ValueError("Unexpected payment-link list")
    if matches:
        return matches[0]
    if row["expires_at"] <= time.time():
        return None
    return api(config, "payment_links", {
        "amount": row["amount"], "currency": "INR", "accept_partial": False,
        "expire_by": row["expires_at"], "reference_id": row["reference"],
        "description": "TableFlow cafe order", "notify": {"sms": False, "email": False},
        "reminder_enable": False,
    })


def reconcile(database, row, config):
    link = link_details(config, row)
    if link is None:
        with database() as db:
            db.execute("UPDATE online_checkouts SET state='closed' WHERE visit_id=? AND reference=? AND state='pending'",
                       (row["visit_id"], row["reference"]))
        return
    link_id = link.get("id", "")
    if (not re.fullmatch(r"plink_[A-Za-z0-9]+", link_id)
            or (row["provider_id"] and row["provider_id"] != link_id)
            or link.get("reference_id") != row["reference"]
            or link.get("currency") != "INR" or link.get("amount") != row["amount"]):
        return
    short_url = link.get("short_url", "")
    url = urlsplit(short_url)
    trusted = (url.scheme == "https" and url.netloc in {"rzp.io", "razorpay.com"}
               and not url.fragment and bool(url.path.strip("/")))
    method = payment_id = None
    if link.get("status") == "paid" and link.get("amount_paid") == row["amount"]:
        payments = link.get("payments") or []
        if len(payments) != 1:
            return
        payment_id = payments[0].get("payment_id") or payments[0].get("id") or ""
        if not re.fullmatch(r"pay_[A-Za-z0-9]+", payment_id):
            return
        payment = api(config, "payments/" + payment_id)
        if (payment.get("id") != payment_id or payment.get("status") != "captured"
                or payment.get("currency") != "INR" or payment.get("amount") != row["amount"]
                or payment.get("amount_refunded") != 0):
            return
        method = METHODS.get(payment.get("method"), "Online")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT * FROM online_checkouts WHERE visit_id=?", (row["visit_id"],)).fetchone()
        if not current or current["reference"] != row["reference"] or current["state"] != "pending":
            return
        db.execute("UPDATE online_checkouts SET provider_id=?, checkout_url=? WHERE visit_id=?",
                   (link_id, short_url if trusted else None, row["visit_id"]))
        if link.get("status") in ("expired", "cancelled") and link.get("amount_paid") == 0:
            db.execute("UPDATE online_checkouts SET state='closed' WHERE visit_id=?", (row["visit_id"],))
        elif method:
            visit = ops.find_visit(db, row["token"])
            if not visit or visit["paid"]:
                return
            ops.confirm_payment(db, visit, {"method": method, "reference": payment_id,
                                            "revision": row["revision"]})
            db.execute("UPDATE online_checkouts SET state='paid' WHERE visit_id=?", (row["visit_id"],))


@contextmanager
def payment_worker(database):
    config = credentials()
    if not config:
        yield
        return
    stop = Event()

    def run():
        while not stop.is_set():
            with database() as db:
                pending = db.execute("""
                    SELECT c.*, v.token FROM online_checkouts c JOIN visits v ON v.id=c.visit_id
                    WHERE c.state='pending' AND c.account_key=? ORDER BY c.visit_id
                """, (config[0],)).fetchall()
            for row in pending:
                if stop.is_set():
                    return
                try:
                    reconcile(database, row, config)
                except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
                    # Retry safely using the same unique provider reference.
                    continue
            stop.wait(5)

    thread = Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
