"""Isolated GUI test server: synthetic vision, temporary data, no model downloads."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import uvicorn
from app.main import create_app
from app.models import ReceiptData


class Engine:
    def recognize(self, image):
        return [
            {
                "id": "0",
                "text": "RIVERSIDE INDUSTRIAL SUPPLY",
                "score": 0.98,
                "box": [20, 20, 600, 60],
            }
        ]


workspace = tempfile.TemporaryDirectory(prefix="receiptiq-ui-qa-")
app = create_app(Path(workspace.name), engine=Engine())


def extract(images, lines, **options):
    draft = ReceiptData(
        supplier="Riverside Industrial Supply",
        receipt_date="2026-10-23",
        receipt_number="00-0045 / 00-0029",
        total="1590.00",
        items=[
            {
                "description": "Galvanized pipe 2 inch",
                "quantity": "1",
                "unit": "pc",
                "unit_price": "1200.00",
                "line_total": "1200.00",
                "item_type": "Materials",
            },
            {
                "description": "Cutting disc for stainless steel",
                "quantity": "5",
                "unit": "pcs",
                "unit_price": "35.00",
                "line_total": "175.00",
                "item_type": "Materials",
            },
            {
                "description": "Paint roller 4 inch",
                "quantity": "3",
                "unit": "pcs",
                "unit_price": "45.00",
                "line_total": "135.00",
                "item_type": "Tools",
            },
            {
                "description": "Delivery charge",
                "quantity": "1",
                "unit": "trip",
                "unit_price": "80.00",
                "line_total": "80.00",
                "item_type": "Logistics",
            },
        ],
    ).model_dump()
    return {
        "draft": draft,
        "model": options["model"],
        "seconds": 0.1,
        "field_meta": {},
        "notice": "Check each item against the receipt before verifying.",
    }


app.state.worker.vision.extract = extract
uvicorn.run(app, host="127.0.0.1", port=8766)
