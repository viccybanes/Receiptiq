"""Import expense CSVs and optional local photos; ZIP paths are never extracted."""

import csv
import hashlib
import io
import stat
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import HTTPException
from pydantic import ValidationError

from .db import now, uid
from .imaging import inspect_file
from .models import ReceiptData

MAX_CSV = 2 * 1024 * 1024
MAX_PHOTO = 25 * 1024 * 1024
MAX_BUNDLE = 200 * 1024 * 1024
MAX_EXPANDED = 500 * 1024 * 1024
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}


def copy_asset(store, source, prefix, created):
    identifier = uid()
    temporary = store.root / "tmp" / (identifier + ".upload")
    try:
        total = 0
        with temporary.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_PHOTO:
                    raise ValueError("Each photo must be no larger than 25 MB.")
                output.write(chunk)
        mime = inspect_file(temporary)
        relative = f"{prefix}/{identifier}" + EXTENSIONS[mime]
        path = store.file(relative)
        temporary.replace(path)
        created.append(path)
        return identifier, relative, mime
    finally:
        temporary.unlink(missing_ok=True)


def add_photo(store, receipt_id, source, name):
    created = []
    try:
        identifier, relative, mime = copy_asset(store, source, "attachments", created)
        if mime == "application/pdf":
            raise ValueError("Choose a JPG or PNG photo.")
        with store.connect() as connection:
            if not connection.execute("SELECT 1 FROM receipts WHERE id=?", (receipt_id,)).fetchone():
                raise HTTPException(404, "Receipt not found")
            connection.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?)",
                               (identifier, receipt_id, relative, name, mime, now()))
        return {"id": identifier, "name": name}
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def safe_member(name):
    path = PurePosixPath(name)
    if "\\" in name or ":" in name or path.is_absolute() or ".." in path.parts:
        raise ValueError("ZIP contains an unsafe file path.")


def import_expenses(store, source, filename, photos):
    archive = None
    created = []
    try:
        source.seek(0, 2)
        if source.tell() > MAX_BUNDLE:
            raise ValueError("Import exceeds the 200 MB limit.")
        source.seek(0)
        if filename.lower().endswith(".zip"):
            archive = zipfile.ZipFile(source)
            entries = archive.infolist()
            if len(entries) > 1500 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise ValueError("ZIP contains too many files or more than 500 MB of expanded data.")
            for entry in entries:
                safe_member(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("ZIP symbolic links are not supported.")
            names = [entry.filename for entry in entries if not entry.is_dir()]
            if len(names) != len(set(names)):
                raise ValueError("ZIP contains duplicate filenames.")
            csv_names = [name for name in names if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise ValueError("ZIP must contain exactly one CSV file.")
            if archive.getinfo(csv_names[0]).file_size > MAX_CSV:
                raise ValueError("CSV exceeds the 2 MB limit.")
            content = archive.read(csv_names[0])
        elif filename.lower().endswith(".csv"):
            content = source.read(MAX_CSV + 1)
            names = [photo.filename for photo in photos]
            if len(names) != len(set(names)):
                raise ValueError("Selected photos must have distinct filenames.")
        else:
            raise ValueError("Choose a CSV file or a ZIP containing CSV and photos.")
        if len(content) > MAX_CSV:
            raise ValueError("CSV exceeds the 2 MB limit.")
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
        fields = reader.fieldnames or []
        required = {"Purchase date", "Vendor", "Description", "Quantity", "Unit price", "Amount"}
        if not required.issubset(fields) or len(fields) != len(set(fields)):
            raise ValueError("CSV needs distinct Purchase date, Vendor, Description, Quantity, Unit price, Amount columns.")
        groups = defaultdict(list)
        for index, row in enumerate(reader):
            if index >= 5000:
                raise ValueError("CSV exceeds 5,000 line items.")
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row does not match its column headers.")
            key = row.get("Receipt ID") or tuple(row.get(field, "") for field in
                                               ("Purchase date", "Vendor", "Receipt number", "Receipt photo"))
            groups[key].append(row)
        if not 1 <= len(groups) <= 500:
            raise ValueError("CSV must contain 1-500 receipts.")

        def open_photo(reference):
            safe_member(reference)
            matches = [name for name in names if name == reference]
            if not matches:
                matches = [name for name in names if PurePosixPath(name).name == PurePosixPath(reference).name]
            if len(matches) > 1:
                raise ValueError(f"Photo filename is ambiguous: {reference}")
            if not matches:
                return None
            if archive:
                return archive.open(matches[0])
            photo = next(photo for photo in photos if photo.filename == matches[0])
            photo.file.seek(0)
            return photo.file

        batch_id = uid()
        warnings = []
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO batches(id,name,created_at) VALUES(?,?,?)",
                               (batch_id, "CSV import · " + filename[:150], now()))
            for rows in groups.values():
                header = rows[0]
                for row in rows[1:]:
                    if any(row.get(field, "") != header.get(field, "") for field in
                           ("Purchase date", "Vendor", "Receipt number", "Receipt photo", "Notes", "Total")):
                        raise ValueError("Rows of the same receipt must agree on receipt details and Total.")
                purchase_date = header["Purchase date"].strip()
                for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        purchase_date = datetime.strptime(purchase_date, pattern).date().isoformat()
                        break
                    except ValueError:
                        continue
                data = ReceiptData.model_validate({
                    "supplier": header["Vendor"], "receipt_date": purchase_date,
                    "receipt_number": header.get("Receipt number", ""), "notes": header.get("Notes", ""),
                    "total": header.get("Total", ""),
                    "items": [{"description": row["Description"], "quantity": row["Quantity"],
                               "unit": row.get("Unit", ""), "unit_price": row["Unit price"],
                               "line_total": row["Amount"], "item_type": row.get("Type") or "Materials"} for row in rows],
                }).model_dump()
                if not data["total"]:
                    from .extraction import decimal
                    data["total"] = str(sum(decimal(item["line_total"]) for item in data["items"]))
                    data["notes"] = (data["notes"] + "\nTotal inferred from Amount values; check against receipt.").strip()[:4000]
                receipt_id, document_id, upload_id = uid(), uid(), uid()
                reference = header.get("Receipt photo", "").strip()
                photo = open_photo(reference) if reference else None
                if photo is not None:
                    try:
                        _, relative, mime = copy_asset(store, photo, "originals", created)
                    finally:
                        if archive:
                            photo.close()
                    original_name = PurePosixPath(reference).name
                else:
                    relative, mime, original_name = f"originals/{document_id}.csv", "text/csv", filename
                    output = io.StringIO(newline="")
                    writer = csv.DictWriter(output, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                    path = store.file(relative)
                    created.append(path)
                    path.write_text(output.getvalue(), encoding="utf-8-sig")
                    if reference:
                        message = f"Receipt photo not provided: {reference}. Add photo manually."
                        warnings.append(message)
                        data["notes"] = (data["notes"] + "\n" + message).strip()[:4000]
                connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?)", (
                    document_id, original_name, relative, hashlib.sha256(store.file(relative).read_bytes()).hexdigest(),
                    mime, now()))
                connection.execute("INSERT INTO batch_files(id,batch_id,name,state,receipt_id) VALUES(?,?,?,?,?)",
                                   (upload_id, batch_id, original_name, "accepted", receipt_id))
                connection.execute("INSERT INTO receipts(id,document_id,batch_id,upload_id,status,revision,data_json,"
                                   "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                   (receipt_id, document_id, batch_id, upload_id, "queued", 1, "{}", now(), now()))
                for item, row in zip(data["items"], rows):
                    reference = row.get("Photo of item purchased", "").strip()
                    if not reference:
                        continue
                    photo = open_photo(reference)
                    if photo is None:
                        message = f"Photo of item purchased not provided: {reference}."
                        warnings.append(message)
                        data["notes"] = (data["notes"] + "\n" + message).strip()[:4000]
                        continue
                    try:
                        identifier, relative, mime = copy_asset(store, photo, "attachments", created)
                    finally:
                        if archive:
                            photo.close()
                    if mime == "application/pdf":
                        raise ValueError("Photo of item purchased must be a JPG or PNG.")
                    connection.execute("INSERT INTO attachments VALUES(?,?,?,?,?,?)",
                                       (identifier, receipt_id, relative, PurePosixPath(reference).name, mime, now()))
                    item["photo_id"] = identifier
                data = store.write_data(connection, receipt_id, data)
                store.audit(connection, receipt_id, 1, "import", after=data, reason="Imported CSV requires verification.")
                connection.execute("INSERT INTO jobs(id,receipt_id,batch_id,state,stage,created_at,updated_at,kind) "
                                   "VALUES(?,?,?,?,?,?,?,?)",
                                   (uid(), receipt_id, batch_id, "queued", "Import preview", now(), now(), "preview"))
        return {"id": batch_id, "receipt_count": len(groups), "warnings": warnings}
    except (ValueError, ValidationError, UnicodeError, csv.Error, zipfile.BadZipFile, RuntimeError) as exc:
        for path in created:
            path.unlink(missing_ok=True)
        raise HTTPException(422, str(exc)) from exc
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        if archive:
            archive.close()
