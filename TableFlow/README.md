# TableFlow restaurant and café setup

TableFlow is a Python and SQLite app for table QR ordering, kitchen tickets,
checkout, sales, invoices and loyalty. It includes sample tables and menu
photos. The same table QR links continue to work after upgrading from the
first version, provided you keep your existing database and network address.

## Run from VS Code

1. Extract the ZIP and open the `TableFlow` folder in VS Code.
2. Choose **Terminal → New Terminal**.
3. Run `python3 app.py` on macOS or Linux, or `py app.py` on Windows.
   Python 3.10+ is sufficient and no packages need to be installed.
4. Open **http://127.0.0.1:8000**. Use **Menu** to set your own dishes, prices and
   HTTPS photo URLs. Use **Tables** to edit your seating and download table QR codes.
5. For an upgrade, stop the older server first. Back up your existing
   `tableflow.sqlite3` and copy it into this folder before starting the app.

## Customer phones and a kitchen screen

Use a trusted local network. Find your computer’s local IPv4 address in its
network settings. Stop the server with Ctrl+C, then start it with that address:

```sh
python3 app.py --lan-ip 192.168.1.25
```

Replace the example IP with your actual address. Keep the server running and
connect customer phones to the same Wi-Fi, or to a network that can reach the
server. The host computer still uses `http://127.0.0.1:8000` for staff screens.
Download the QR codes after switching to LAN mode. If the server’s address or
port changes, download updated QR codes.

To open the kitchen screen on a second local device, start the app with a
long random staff key. On Mac or Linux:

```sh
export TABLEFLOW_STAFF_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s\n' "$TABLEFLOW_STAFF_KEY"
python3 app.py --lan-ip 192.168.1.25
```

Open `http://192.168.1.25:8000/kitchen` on the kitchen device. Use username
`staff` and the generated key. Without a staff key, staff screens are
restricted to the host computer. LAN mode uses HTTP, so keep staff access on
a trusted network and verify the setup with sample orders. Public deployment
requires HTTPS and production-grade staff authentication.

## A complete sample visit

1. A guest scans a table QR, enters party size, sets item quantities and
   sends the order to the kitchen. One phone controls the active bill at a
   table. That phone can open **Add more items** to send another ticket.
2. **Kitchen** refreshes every eight seconds. Mark each ticket Preparing,
   Ready and Served. The guest can refresh their page for status.
3. The guest views order status and can add more items while the visit
   remains open. The customer menu has no checkout or payment form.
4. The manager opens **Sales & checkout**, verifies payment and selects
   **Cash, Card or UPI**. Enter a mobile number for loyalty if the customer
   provides one, and enable WhatsApp only with the customer's approval.
   Collect payment through your existing cash drawer, card terminal or UPI app.
5. The invoice opens on the manager's screen. Use browser Print to save a
   PDF or download the invoice PNG and attach it in WhatsApp. The table remains Occupied after payment. Once guests leave, set it to
   Cleaning, then Available when ready. The sales dashboard updates using Indian Standard Time.
6. Each paid visit with a mobile number earns one stamp. Every tenth paid
   visit creates one free Chocolate Brownie kitchen ticket, adds the
   free item to that invoice and starts the next loyalty cycle. The reward
   is earned independently of leaving a Google review.

Invoices are basic sales invoices. Business identity, GST calculation and
tax-specific fields are not configured in this demo.

## Google review link and WhatsApp

Copy your business’s Google review link and set it before starting TableFlow.
For example, on Mac or Linux:

```sh
export TABLEFLOW_REVIEW_URL='https://g.page/r/your-business/review'
```

The manager can choose **Download invoice photo (PNG)** on a paid invoice.
Photo generation on macOS uses Swift from Apple's Command Line Tools. If
they are missing, run `xcode-select --install` in Terminal. Existing HTML
and printed invoices still work on other platforms.

The manager can open the customer's WhatsApp chat and attach the downloaded
PNG. To send the photo automatically on payment confirmation, configure
the WhatsApp Business Cloud API and an approved template with an **Image**
header and a fixed body with no parameters, for example
**Thank you for visiting. Your invoice is attached.**

```sh
export TABLEFLOW_WA_VERSION='vXX.0'
export TABLEFLOW_WA_PHONE_ID='your-numeric-phone-number-id'
export TABLEFLOW_WA_TOKEN='your-secret-access-token'
export TABLEFLOW_WA_IMAGE_TEMPLATE='tableflow_invoice_photo'
export TABLEFLOW_WA_LANGUAGE='en'
```

Use your supported Graph API version and the template's exact approved
language. Run `python3 app.py` after setting these variables. Automatic delivery
uploads the PNG and attaches it to the message template for opted-in
customers. The Google review link remains visible on the invoice page when
configured. Demo mode does not send to real WhatsApp numbers.

A status of **Accepted by WhatsApp** means the API accepted the request.
For failures before sending, use **Send via WhatsApp Business** to retry.
Check WhatsApp before retrying a message with uncertain delivery.

The SQLite database holds bills and mobile numbers. Back it up and keep it
accessible only to authorized staff. Store API secrets in your local
environment, not in source code or screenshots.

## Test with customer mobile data

On Mac with Homebrew, run `brew install cloudflared` once. Alternatively,
get cloudflared from the official Cloudflare downloads page.

Run `python3 demo.py` in the TableFlow folder. The demo starts a local staff
dashboard at http://127.0.0.1:8001 and a temporary HTTPS tunnel for the guest
routes. It uses fresh sample data and ignores WhatsApp and review settings.

After **Customer HTTPS base** appears, open the demo dashboard and choose
**View QR code** for a table. Turn Wi-Fi off on your phone, scan the QR or
open the full table menu link using mobile data, and place one test order.
On the Mac, open http://127.0.0.1:8001/kitchen and check for the order.
Use sample orders only. Anyone with a demo table link can order on that demo
table while the tunnel is active. Staff routes stay on your Mac.

Press Control+C to stop the demo. The tunnel URL and sample data are
temporary. The next run creates a fresh URL and QR.

## Notify customers when an order is served

The customer's open order page refreshes every eight seconds until all of
its tickets are marked Served. After handing over the food, select
**Mark served** in Kitchen.

For WhatsApp delivery, configure the same Cloud API sender and language
used for invoices, plus a separate approved text template with no variables.
Example body: **Your order has been served. Enjoy your meal!**

```sh
export TABLEFLOW_WA_SERVED_TEMPLATE='tableflow_order_served'
```

With the sender and template configured, the menu offers an optional phone
number and opt-in for that order. No number is required to order. The message
is queued only when that opted-in kitchen ticket moves from Ready to Served.
Kitchen shows the recent send results. API acceptance does not confirm
delivery to the handset. The public demo disables WhatsApp credentials, so
use it to test the on-page status only. Use the configured local app for
real WhatsApp delivery.

## Staff attendance and monthly payroll

Open **Staff** in the manager dashboard. Add employees with their roles,
start dates and default monthly salary in INR. Use **Manage employee work
periods** to record a last working day. Select a date to mark Present, Half
day, Absent, Paid leave or Weekly off.

Open **Monthly payroll** to select a month, review each employee's attendance
and pay, add a bonus or other deduction, and override the salary for that
month. The report uses calendar-day proration. Present, Paid leave and
Weekly off count as one paid day. Half day counts as half. Absent counts as
zero. Unmarked or upcoming days leave the report incomplete.

Once all eligible days in the month are marked, use **Record completed
payment** after paying the employee. Save any changes and review the total
before marking it paid. This records the amount and date in SQLite and
locks that employee's attendance and payroll for the month. TableFlow
records payment but does not transfer salary. Download the monthly CSV for
Excel or your records. Payroll calculations do not include statutory
deductions automatically.

Run the normal app for records you want to keep. The demo uses a temporary
database and removes demo data when it closes. If upgrading, keep your
existing `tableflow.sqlite3`; the staff tables are created automatically.

## Take customer orders from other networks and keep the records

Run the following from the TableFlow folder on your Mac. It assumes
`cloudflared` is already installed.

```sh
PATH="$HOME/.local/bin:$PATH" python3 demo.py --live
```

After **Customer HTTPS base** appears, open the manager dashboard at
http://127.0.0.1:8001 on the Mac. From **Tables**, open **View QR code**.
Customers can scan that table's QR using their own mobile data or Wi-Fi.
Orders appear in **Kitchen** and use the same SQLite file as `app.py`.
You can use the same database for staff attendance and payroll. Existing
WhatsApp settings are used in this mode if configured.

Keep the Mac awake, connected to the internet, and the terminal process
running. Share only the table QR or its full menu link with customers.
Anyone with a table menu link can submit an order to that table. The
temporary public address changes after a restart, so use fresh QR codes
after each launch. This mode is suitable for a pilot. For permanently
printed QR codes, use a stable HTTPS hostname with a named tunnel or
hosting, and keep the manager dashboard on a private connection.

### Permanent table QR codes with a named tunnel

Run TableFlow with your café’s actual customer HTTPS hostname:

```sh
python3 demo.py --live --public-url https://menu.example.com
```

In the named tunnel's published application settings, route that hostname
to the guest service at `http://127.0.0.1:8002`. Keep the manager dashboard
at `http://127.0.0.1:8001` on your Mac. Start the named tunnel separately.
Test each QR from a phone on mobile data before printing. Keep the same
hostname and SQLite database to reuse printed table QR codes.

### Order and billing corrections

- Kitchen can cancel an unserved ticket on an unpaid bill with a reason.
  Cancelled tickets are excluded from checkout totals. If all tickets are
  cancelled, the checkout closes. Any old add-items link expires.
- Checkout includes a customer add-items link staff can share with others
  seated at that table.
- An issued invoice allows a manager to correct the recorded payment method
  with a reason. The history stays on the invoice. The correction updates
  the sales breakdown. It does not change the amount or issue a refund.
- Use the date selector under Sales to review previous transactions.
- Payroll lists upcoming and unmarked days separately from actual attendance
  deductions. Pay estimates consider marked attendance only.

## Customer UPI and waiter requests

To offer UPI in live mode, start TableFlow with your café’s verified merchant VPA:

```sh
export TABLEFLOW_UPI_ID='your-merchant@yourupi'
export TABLEFLOW_UPI_NAME='Your Cafe'
PATH="$HOME/.local/bin:$PATH" python3 demo.py --live
```

After ordering, customers choose **Continue to UPI** and **Open UPI app**.
This starts an external payment request with the current bill amount. Staff
must verify receipt in the merchant UPI app before confirming checkout.
TableFlow does not automatically verify or collect funds. The demo mode
without `--live` disables real UPI payment links. If changing a bill after
someone started UPI checkout, first check that the customer has not paid.
Then use **Reopen order** in the manager's checkout.

Guests can choose **Call waiter** on the menu or order page. Staff see
pending requests in **Waiter calls** and select **Mark handled** after
responding. Repeated taps while a call is pending keep one request per table.

## Direct table QR ordering
Customers scan a table QR, choose dishes and send the order directly to
Kitchen. The printed QR includes the table identity. Anyone with its link
can send an order while the table can receive orders. A table marked
Cleaning cannot accept orders or waiter calls. Keep the QR at the table.

The staff navigation shows a live count of unresolved waiter calls. After
paying in their UPI app, customers select **I have paid** to hide the
payment button and wait for staff verification. This customer report never
records a confirmed payment. Staff can check receipt or reopen an abandoned
UPI attempt from Checkout.

## Razorpay online checkout with automatic verification

Use the café’s Razorpay account and enable its intended payment methods.
An order gets an amount-specific hosted payment link. The QR opens the
same secure checkout as the link, where customers can choose available
methods such as UPI or net banking. The QR is a checkout-link QR, not a
static UPI scanner.

Set the key ID and secret for the running process:

```sh
export TABLEFLOW_RAZORPAY_KEY_ID='your-key-id'
export TABLEFLOW_RAZORPAY_KEY_SECRET='your-key-secret'
```

Use Razorpay **Test** keys with `python3 demo.py` for a temporary database.
Use **Live** keys with `python3 demo.py --live` (or `python3 app.py`) for
real payments. Keep keys out of source code, screenshots and chat. Keep
TableFlow running so it can check the payment status. The app records a
payment and updates Sales only after Razorpay reports capture and the
bill amount, currency and references match. This uses server-side polling.
Verify available methods and test the flow before accepting café payments.

## Restaurant service workflow

1. A customer scans the table QR, browses the dish details and selects **Place order**.
2. Staff go to **Orders** and select **Accept and send to kitchen**, or
   decline with a reason. Pending orders cannot be paid.
3. **Kitchen** displays accepted orders, elapsed time, station labels and
   filters. Each kitchen ticket advances through Preparing, Ready and Served.
   Status applies to the whole ticket.
4. **Floor** shows tables and highlights waiter calls. The overview refreshes
   every eight seconds.
5. The order page allows a tip and an equal-split estimate before payment.
   It shows the receipt after payment and the configured Google review link.
   One transaction settles the bill. The split estimate does not charge each
   guest individually.

Enter allergen information, spice level, serving size, offer text and kitchen
station in **Menu**. Use separate entries if sizes have different prices.
If configured, seated guests can see the guest Wi-Fi details from their
order page using `TABLEFLOW_GUEST_WIFI_NAME` and `TABLEFLOW_GUEST_WIFI_PASSWORD`.
Use a guest network. A shared order link can also show those details.

### Pay at the table after service

After Kitchen marks every active ticket served, the customer's existing
order page displays the bill and its payment options. Cancelled tickets do
not delay checkout. The customer can stay at the table and choose **Pay
bill** for Razorpay or **Pay bill with UPI** when the manual UPI option is
configured. Without either integration, the order page directs the customer
to **Call waiter**. Staff can still record a counter payment after order
approval.

### Direct UPI without a gateway

Direct UPI takes priority for new customer payments when
`TABLEFLOW_UPI_ID` and `TABLEFLOW_UPI_NAME` are configured. No Razorpay
account or API keys are required for direct UPI. Existing Razorpay
checkouts may still finish if their original keys remain configured.

After every active ticket is served, guests choose **Pay bill with UPI**.
The next page shows the café merchant name, UPI ID, amount and payment
reference. Guests can open a compatible UPI app on the same device or
scan the displayed payment QR using another device. A very long encoded
merchant name or address may exceed the built-in QR capacity; **Open UPI
app** is still available in that case. After paying, guests tap
**I have paid**. Verify the incoming payment in the café's merchant app
or bank before marking the bill paid in TableFlow.

## Monthly sales and inventory

In **Sales & checkout → Monthly sales reports**, select a month to see sales excluding tips, tips, total collections, payment methods, visits, guests and daily totals. **Download CSV** exports the report. Payments are grouped by the date received in India Standard Time.

In **Inventory**, add each grocery, ingredient or supply with a unit, opening stock and low-stock threshold. Open the item to record a purchase, usage or waste. Purchase cost is optional. Movement history filters by month and item and shows recorded purchase costs. Stock quantities update from staff records.
# RestaurantManagement
# restaurant
