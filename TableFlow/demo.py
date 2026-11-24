"""Serve TableFlow customer orders through an HTTPS tunnel."""

import argparse
import os
import re
import shutil
import subprocess
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from app import Handler, database, initialize
from online import payment_worker


class GuestHandler(Handler):
    def is_staff(self):
        return False


def watch_tunnel(process, staff):
    for line in process.stdout:
        if match := re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line):
            staff.menu_base = match[0]
            print(f"Customer HTTPS base: {match[0]}", flush=True)
            print("Open View QR code for a table in the staff dashboard.", flush=True)
    staff.menu_base = None
    print("The customer HTTPS tunnel stopped. Press Control+C and restart to get a new link.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Keep orders and settings in the regular TableFlow database.")
    parser.add_argument("--public-url", help="Stable HTTPS customer hostname for an external named tunnel.")
    args = parser.parse_args()
    if args.public_url and not args.live:
        parser.error("--public-url requires --live")
    if args.public_url and not re.fullmatch(r"https://(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}", args.public_url):
        parser.error("Use an HTTPS hostname such as https://menu.example.com")
    if args.public_url and len(args.public_url) + len("/menu/") + 16 > 106:
        parser.error("Use a shorter hostname so its table links fit in the QR code")
    cloudflared = shutil.which("cloudflared")
    if not cloudflared and not args.public_url:
        raise SystemExit("Install cloudflared first (on Mac with Homebrew: brew install cloudflared).")
    storage = nullcontext() if args.live else TemporaryDirectory(prefix="tableflow-demo-")
    with storage as folder:
        if folder is not None:
            os.environ["TABLEFLOW_DB"] = str(Path(folder) / "demo.sqlite3")
            for key in list(os.environ):
                if key.startswith("TABLEFLOW_WA_") or key == "TABLEFLOW_REVIEW_URL" or key.startswith(("TABLEFLOW_UPI_", "TABLEFLOW_GUEST_WIFI_")):
                    os.environ.pop(key)
        os.environ["TABLEFLOW_PAYMENT_SANDBOX"] = "0" if args.live else "1"
        initialize()
        with ThreadingHTTPServer(("127.0.0.1", 8001), Handler) as staff, \
                ThreadingHTTPServer(("127.0.0.1", 8002 if args.public_url else 0), GuestHandler) as guest:
            staff.menu_base = args.public_url
            guest_url = f"http://127.0.0.1:{guest.server_port}"
            mode = "Live" if args.live else "Demo"
            print(f"{mode} staff dashboard: http://127.0.0.1:8001", flush=True)
            process = watcher = None
            if args.public_url:
                print(f"Customer HTTPS base: {args.public_url}", flush=True)
                print(f"Route the named tunnel to the customer service at {guest_url}", flush=True)
            else:
                print("Waiting for a temporary customer HTTPS address…", flush=True)
                process = subprocess.Popen(
                    [cloudflared, "tunnel", "--url", guest_url],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                )
                watcher = Thread(target=watch_tunnel, args=(process, staff), daemon=True)
                watcher.start()
            guest_thread = Thread(target=guest.serve_forever, daemon=True)
            guest_thread.start()
            try:
                with payment_worker(database):
                    staff.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                guest.shutdown()
                guest_thread.join()
                if watcher is not None:
                    watcher.join()


if __name__ == "__main__":
    main()
