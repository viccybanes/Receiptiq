"""Validated API input and editable receipt values."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Item(StrictModel):
    description: str = Field(default="", max_length=2000)
    quantity: str = Field(default="", max_length=32)
    unit_price: str = Field(default="", max_length=32)
    line_total: str = Field(default="", max_length=32)


class ReceiptData(StrictModel):
    supplier: str = Field(default="", max_length=500)
    receipt_number: str = Field(default="", max_length=100)
    receipt_date: str = Field(default="", max_length=32)
    currency: str = Field(default="", max_length=3)
    subtotal: str = Field(default="", max_length=32)
    tax: str = Field(default="", max_length=32)
    discount: str = Field(default="", max_length=32)
    total: str = Field(default="", max_length=32)
    items: list[Item] = Field(default_factory=list, max_length=1000)


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


class Process(StrictModel):
    rotation: Literal[0, 90, 180, 270] = 0


class ExportInput(StrictModel):
    receipt_ids: list[str] = Field(min_length=1, max_length=500)


def blank_data():
    return ReceiptData().model_dump()
