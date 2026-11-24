import json
import shutil
import subprocess
from pathlib import Path
from tempfile import gettempdir

from operations import REWARD_ITEM, local_time, money


def render_invoice(row, items):
    swift = shutil.which("swift")
    if not swift:
        raise OSError("Install Apple's Command Line Tools to create invoice photos.")
    cache = Path(gettempdir()) / "tableflow-swift-cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    data = {
        "number": f'TF-{row["visit_id"]:06}',
        "table": row["table_name"],
        "date": local_time(row["paid_at"]),
        "payment": row["method"],
        "total": money(row["total"]),
        "reward": f"Free {REWARD_ITEM} · Loyalty reward" if row["reward"] else "",
        "items": [
            {"name": item["name"], "quantity": item["quantity"],
             "amount": money(item["price"] * item["quantity"])}
            for item in items
        ],
    }
    try:
        result = subprocess.run(
            [swift, "-module-cache-path", str(cache), str(Path(__file__).with_name("invoice_image.swift"))],
            input=json.dumps(data).encode(), capture_output=True, timeout=60, check=True,
        )
    except subprocess.SubprocessError as error:
        raise OSError("Invoice photo could not be generated. Check Apple's Command Line Tools.") from error
    if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise OSError("The invoice renderer did not return a PNG.")
    return result.stdout
