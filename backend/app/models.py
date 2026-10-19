"""Validated API input and editable receipt values."""

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Item(StrictModel):
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=36)
    item_number: int | None = Field(default=None, ge=1)
    description: str = Field(default="", max_length=2000)
    quantity: str = Field(default="", max_length=32)
    unit: str = Field(default="", max_length=50)
    unit_price: str = Field(default="", max_length=32)
    line_total: str = Field(default="", max_length=32)
    item_type: str = Field(default="Materials", min_length=1, max_length=100)
    photo_id: str = Field(default="", max_length=36)


class ReceiptData(StrictModel):
    supplier: str = Field(default="", max_length=500)
    receipt_number: str = Field(default="", max_length=500)
    receipt_date: str = Field(default="", max_length=32)
    currency: str = Field(default="PHP", max_length=3)
    notes: str = Field(default="", max_length=4000)
    receipt_photo_id: str = Field(default="", max_length=36)
    subtotal: str = Field(default="", max_length=32)
    tax: str = Field(default="", max_length=32)
    discount: str = Field(default="", max_length=32)
    total: str = Field(default="", max_length=32)
    items: list[Item] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Each line item must have a distinct ID.")
        return self


class BatchInput(StrictModel):
    name: str = Field(default="Receipt batch", max_length=200)
    files: list[str] = Field(min_length=1, max_length=500)


class Edit(StrictModel):
    revision: int = Field(ge=0)
    data: ReceiptData


class Verify(StrictModel):
    revision: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=100)
    acknowledged: list[str] = Field(default_factory=list, max_length=2000)
    reason: str = Field(default="", max_length=2000)
    retrieval_allowed: bool | None = None


class Process(StrictModel):
    rotation: Literal[0, 90, 180, 270] = 0


class ExportInput(StrictModel):
    receipt_ids: list[str] = Field(min_length=1, max_length=500)
    include_photos: bool = False
    template_id: str = Field(default="default", min_length=1, max_length=100)
    template_version: int | None = Field(default=None, ge=1)


class VisionInput(StrictModel):
    revision: int = Field(ge=0)
    page_id: str = Field(min_length=1, max_length=36)
    crop: list[float] | None = Field(default=None, min_length=4, max_length=4)
    model: Literal["qwen3-vl:2b-instruct", "qwen3-vl:4b-instruct"] = "qwen3-vl:2b-instruct"

    @model_validator(mode="after")
    def valid_crop(self):
        if self.crop is not None:
            left, top, right, bottom = self.crop
            if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError("Select a nonempty region inside the receipt image.")
        return self


class RevisionInput(StrictModel):
    revision: int = Field(ge=0)


class ReviewOptions(RevisionInput):
    assignee: str = Field(default="", max_length=100)
    second_review: bool = False
    retrieval_allowed: bool = False


class SetupInput(StrictModel):
    profile: Literal["ocr-only", "2b", "4b"] = "2b"


class LocationInput(StrictModel):
    target: str = Field(min_length=3, max_length=2048)


class SplitInput(RevisionInput):
    groups: list[list[int]] = Field(min_length=1, max_length=30)


def blank_data():
    return ReceiptData().model_dump()
