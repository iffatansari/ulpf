"""
schema/parser_record.py

The stored shape of a parser registration.

The registry is metadata and control over the orchestrator's real parser
chain. It never holds executable code: a built-in parser is referenced by
id, and a custom one is a list of named regex field rules.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# A parser is either in the chain, held back, or not in use yet.
PARSER_STATUSES = ("active", "disabled", "draft")

# Built-in ids are "<name>-parser-v<major>"; custom ones must not collide with
# that shape so an id in the registry can never be mistaken for built-in code.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_id(value: str) -> str:
    if not value or not _ID_RE.match(value):
        raise ValueError(
            "parser_id must be lowercase alphanumerics and hyphens, "
            "starting alphanumeric, 1-64 characters"
        )
    return value


def _validate_display_name(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("display_name must not be blank")
    return value.strip()


class FieldRule(BaseModel):
    """
    One named extraction rule for a custom parser.

    `pattern` is a regular expression applied to the raw line. It is data, not
    code, and is length-bounded in parsers.registry before it is compiled.
    """

    name: str = Field(..., description="Field to populate, e.g. 'src_ip'")
    pattern: str = Field(..., description="Regular expression with one capture group")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field rule name must not be blank")
        return value.strip()

    @field_validator("pattern")
    @classmethod
    def _pattern_not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field rule pattern must not be blank")
        return value.strip()


class ParserRecordBase(BaseModel):
    display_name: str
    description: Optional[str] = None
    status: str = Field(default="draft", description="one of " + ", ".join(PARSER_STATUSES))
    priority: int = Field(default=100, description="lower is attempted earlier")
    source_formats: List[str] = Field(default_factory=list)
    field_rules: List[FieldRule] = Field(default_factory=list)
    sample_payload: Optional[str] = None

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _validate_display_name(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in PARSER_STATUSES:
            raise ValueError(
                f"status must be one of {PARSER_STATUSES}, got {value!r}"
            )
        return value

    @field_validator("source_formats")
    @classmethod
    def _formats(cls, value: List[str]) -> List[str]:
        return [str(v).strip().lower() for v in value if str(v).strip()]


class ParserRecord(ParserRecordBase):
    """A parser as stored in ulpf-parsers."""

    parser_id: str
    version: int = 1
    is_builtin: bool = False
    # Bounded snapshot list, oldest first, used by rollback.
    history: List[Dict[str, Any]] = Field(default_factory=list)
    last_test: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    created_by: str = "system"

    @field_validator("parser_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _validate_id(value)


class ParserCreate(BaseModel):
    """Body for POST /parsers."""

    parser_id: str
    display_name: str
    description: Optional[str] = None
    status: str = "draft"
    priority: int = 100
    source_formats: List[str] = Field(default_factory=list)
    field_rules: List[FieldRule] = Field(default_factory=list)
    sample_payload: Optional[str] = None
    created_by: str = "ui"

    @field_validator("parser_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _validate_id(value)

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _validate_display_name(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in PARSER_STATUSES:
            raise ValueError(
                f"status must be one of {PARSER_STATUSES}, got {value!r}"
            )
        return value


class ParserUpdate(BaseModel):
    """
    Body for PUT /parsers/{parser_id}.

    Deliberately omits parser_id and is_builtin: a parser's identity and its
    built-in-ness are not editable, and extra keys are ignored rather than
    applied.
    """

    display_name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    source_formats: Optional[List[str]] = None
    field_rules: Optional[List[FieldRule]] = None
    sample_payload: Optional[str] = None

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _validate_display_name(value)

    @field_validator("status")
    @classmethod
    def _status(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in PARSER_STATUSES:
            raise ValueError(
                f"status must be one of {PARSER_STATUSES}, got {value!r}"
            )
        return value


class ParserTestRequest(BaseModel):
    """
    Body for POST /parsers/test.

    `sample` is not validated as non-empty here so the route can answer with
    the same 422 shape FastAPI would produce for a bad body.
    """

    parser_id: str
    sample: str
    source_id: str = "parser-test"
    raw_event_id: str = "parser-test-raw"
