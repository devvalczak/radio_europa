"""Declarative widget blocks.

A module's widget handler returns a list of blocks; the shell renders them, so
every widget looks the same and works without module-supplied JavaScript.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator


def _safe_href(v: str | None) -> str | None:
    if v is not None and not (v.startswith("#/") or v.startswith("/") or v.startswith("https://")):
        raise ValueError("href must start with '#/', '/' or 'https://'")
    return v


class ListItem(BaseModel):
    title: str
    subtitle: str | None = None
    badge: str | None = None
    href: str | None = None
    time: str | None = None  # ISO-8601 UTC ('...Z'); the browser shows it in the viewer's timezone
    all_day: bool = False  # with `time`: show the date only
    _href = field_validator("href")(_safe_href)


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    items: list[ListItem]
    empty: str | None = None


class StatBlock(BaseModel):
    type: Literal["stat"] = "stat"
    label: str
    value: str
    hint: str | None = None


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ProgressBlock(BaseModel):
    type: Literal["progress"] = "progress"
    label: str
    value: float = Field(ge=0, le=1)


class ButtonSpec(BaseModel):
    label: str
    href: str | None = None
    action: str | None = None  # "<module>.<action>", run through the hub
    input: dict | None = None
    _href = field_validator("href")(_safe_href)


class ButtonsBlock(BaseModel):
    type: Literal["buttons"] = "buttons"
    buttons: list[ButtonSpec]


Block = Annotated[
    Union[ListBlock, StatBlock, TextBlock, ProgressBlock, ButtonsBlock],
    Field(discriminator="type"),
]
