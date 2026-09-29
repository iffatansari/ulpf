"""
api/routes/parsers.py — the parser registry.

The orchestrator owns the parsing. This module is metadata and control over
it: every built-in entry references the real callable through
parsers.registry, so POST /parsers/test executes the same code the pipeline
does. Nothing here reimplements a parser, and a custom parser is a list of
named regex field rules rather than uploaded code.

Routes are plain functions taking an optional `es`, so they can be called
directly in tests without a TestClient.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from opensearchpy import OpenSearch

# Reuse the exact parser modules the orchestrator process imports, the same
# way routes/drain.py reuses drain_miner. Resolve relative to this file so it
# works both in the container (PYTHONPATH=/app, /app/orchestrator) and in a
# local checkout.
_ORCHESTRATOR = Path(__file__).resolve().parents[2] / "orchestrator"
if str(_ORCHESTRATOR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR))

from parsers import registry  # noqa: E402

from db import PARSERS_INDEX, get_opensearch_client  # noqa: E402
from schema.parser_record import (  # noqa: E402
    ParserCreate,
    ParserRecord,
    ParserTestRequest,
    ParserUpdate,
)

router = APIRouter()

# How many past versions to keep for rollback. Bounded so an edit loop cannot
# grow a document without limit.
MAX_HISTORY = 10

# Every write in this module waits for the next index refresh.
#
# OpenSearch is near-real-time: an indexed document is invisible to `search`
# until the next refresh (1s by default). The registry is read through
# `search` (list_parsers), so without this an operator who registers a parser
# and then reloads the page sees the old list -- the write succeeded and the
# UI reports "gone". A registration that cannot be read back is not a
# registration, and the parser registry is a low-QPS control surface, so
# blocking on the refresh costs nothing and makes write-then-read honest.
REFRESH = "wait_for"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _es(ex: Optional[OpenSearch] = None) -> OpenSearch:
    return ex or get_opensearch_client()


def _serialize(record: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in (record or {}).items() if value is not None}


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def ensure_builtin_parsers(es: Optional[OpenSearch] = None) -> None:
    """
    Register any built-in parser that is not in ulpf-parsers yet.

    Only ever inserts. An existing record is left exactly as it is, so an
    operator's rename, retune or status change survives a restart -- the
    registry is a control surface, and re-seeding must not undo it.
    """
    client = _es(es)
    for parser_id, spec in registry.BUILTIN_PARSERS.items():
        if client.exists(index=PARSERS_INDEX, id=parser_id):
            continue
        record = ParserRecord(
            parser_id=parser_id,
            display_name=spec["display_name"],
            description=spec["description"],
            status="active",
            priority=spec["priority"],
            source_formats=list(spec.get("source_formats", [])),
            sample_payload=spec.get("sample_payload"),
            is_builtin=True,
            created_by="system",
        )
        client.index(
            index=PARSERS_INDEX,
            id=parser_id,
            body=jsonable(record.model_dump()),
            refresh=REFRESH,
        )


def jsonable(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Render datetimes as ISO strings so the body is JSON-serializable."""
    out: Dict[str, Any] = {}
    for key, value in payload.items():
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_parsers(
    include_disabled: bool = False, es: Optional[OpenSearch] = None
) -> List[Dict[str, Any]]:
    """
    All registered parsers in the order the chain would try them: priority
    ascending, then display name.
    """
    client = _es(es)
    try:
        res = client.search(
            index=PARSERS_INDEX,
            body={"size": 500, "query": {"match_all": {}}},
        )
    except Exception as exc:
        # An absent index means nothing is registered yet, which is a normal
        # state on a brand-new stack, not a server error.
        if "index_not_found" in str(exc) or "NotFoundError" in type(exc).__name__:
            return []
        raise

    records = [_serialize(hit.get("_source", {})) for hit in res["hits"]["hits"]]
    if not include_disabled:
        records = [r for r in records if r.get("status") != "disabled"]
    records.sort(key=lambda r: (r.get("priority", 100), r.get("display_name", "")))
    return records


def get_parser(parser_id: str, es: Optional[OpenSearch] = None) -> Optional[Dict[str, Any]]:
    client = _es(es)
    try:
        if not client.exists(index=PARSERS_INDEX, id=parser_id):
            return None
        doc = client.get(index=PARSERS_INDEX, id=parser_id)
    except Exception as exc:
        if "index_not_found" in str(exc) or "NotFoundError" in type(exc).__name__:
            return None
        raise
    return _serialize(doc.get("_source", {}))


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def create_parser(payload: ParserCreate, es: Optional[OpenSearch] = None) -> Dict[str, Any]:
    """
    Register a custom parser.

    A custom parser is declarative field_rules, never code. Creating one does
    not put it in the chain: it starts as 'draft' and must be activated.
    """
    client = _es(es)
    if get_parser(payload.parser_id, es=client) is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"parser_id {payload.parser_id!r} is already registered"
            ),
        )

    record = ParserRecord(
        parser_id=payload.parser_id,
        display_name=payload.display_name,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        source_formats=payload.source_formats,
        field_rules=payload.field_rules,
        sample_payload=payload.sample_payload,
        created_by=payload.created_by,
    )
    body = jsonable(record.model_dump())
    client.index(index=PARSERS_INDEX, id=payload.parser_id, body=body, refresh=REFRESH)
    return _serialize(body)


def update_parser(
    parser_id: str, payload: ParserUpdate, es: Optional[OpenSearch] = None
) -> Dict[str, Any]:
    """
    Update a parser, snapshotting the previous definition for rollback.

    A built-in's identity and executability are untouched: this changes its
    metadata and status only. The orchestrator still runs the same callable.
    """
    client = _es(es)
    current = get_parser(parser_id, es=client)
    if current is None:
        raise HTTPException(status_code=404, detail=f"unknown parser {parser_id!r}")

    changes = payload.model_dump(exclude_none=True)
    if not changes:
        return current

    # Snapshot before overwriting so rollback has something to restore.
    history = list(current.get("history", []))
    history.append(
        {
            "version": current.get("version", 1),
            "saved_at": _now(),
            "definition": {
                key: current.get(key)
                for key in (
                    "display_name",
                    "description",
                    "status",
                    "priority",
                    "source_formats",
                    "field_rules",
                    "sample_payload",
                )
                if current.get(key) is not None
            },
        }
    )
    history = history[-MAX_HISTORY:]

    updated = {**current, **changes, "history": history, "updated_at": _now()}
    client.index(index=PARSERS_INDEX, id=parser_id, body=jsonable(updated), refresh=REFRESH)
    return _serialize(updated)


def rollback_parser(
    parser_id: str, es: Optional[OpenSearch] = None
) -> Dict[str, Any]:
    """
    Restore the previous saved definition of a parser.
    """
    client = _es(es)
    current = get_parser(parser_id, es=client)
    if current is None:
        raise HTTPException(status_code=404, detail=f"unknown parser {parser_id!r}")

    history = list(current.get("history", []))
    if not history:
        raise HTTPException(
            status_code=409, detail=f"{parser_id!r} has no earlier version to roll back to"
        )

    snapshot = history.pop()
    restored = {
        **current,
        **snapshot.get("definition", {}),
        "history": history,
        "version": int(current.get("version", 1)) + 1,
        "updated_at": _now(),
    }
    client.index(index=PARSERS_INDEX, id=parser_id, body=jsonable(restored), refresh=REFRESH)
    return _serialize(restored)


def delete_parser(parser_id: str, es: Optional[OpenSearch] = None) -> Dict[str, Any]:
    """
    Remove a custom parser.

    A built-in cannot be deleted, only disabled: deleting the registration of
    a parser the orchestrator really runs would leave the chain referencing
    something the API no longer knows about.
    """
    client = _es(es)
    current = get_parser(parser_id, es=client)
    if current is None:
        raise HTTPException(status_code=404, detail=f"unknown parser {parser_id!r}")
    if current.get("is_builtin"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"{parser_id!r} is built in; set status to 'disabled' instead "
                "of deleting it"
            ),
        )
    client.delete(index=PARSERS_INDEX, id=parser_id, refresh=REFRESH)
    return {"deleted": parser_id}


# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------


def test_parser(
    payload: ParserTestRequest, es: Optional[OpenSearch] = None
) -> Dict[str, Any]:
    """
    Run a sample payload through a real parser and report what came out.

    For a built-in this executes the orchestrator's own callable, so the
    answer is what the pipeline would do. For a custom parser it applies the
    declarative field rules, and says so, because those rules are not the
    same thing as a parser and a quiet false negative would read as "this
    parser is broken".
    """
    if not payload.sample.strip():
        raise HTTPException(status_code=422, detail="sample must not be empty")

    client = _es(es)
    record = get_parser(payload.parser_id, es=client)

    builtin = record is not None and record.get("is_builtin")
    if record is None and not registry.get_builtin(payload.parser_id):
        raise HTTPException(
            status_code=404, detail=f"unknown parser {payload.parser_id!r}"
        )

    result: Dict[str, Any]
    if builtin or record is None:
        try:
            event = registry.run_parser(
                payload.parser_id,
                payload.sample,
                payload.raw_event_id,
                payload.source_id,
            )
        except Exception as exc:
            # A parser blowing up is a real answer about the sample, not a
            # crash: report it instead of 500-ing the dashboard.
            result = {
                "parser_id": payload.parser_id,
                "matched": False,
                "event": None,
                "error": str(exc),
                "reason": f"parser raised {type(exc).__name__}",
            }
            _record_test(client, payload.parser_id, result)
            return result
        result = {
            "parser_id": payload.parser_id,
            "matched": event is not None,
            "event": jsonable(event.model_dump()) if event is not None else None,
            "error": None,
            "reason": (
                None
                if event is not None
                else "parser declined this sample; it is not its format"
            ),
        }
    else:
        rules = record.get("field_rules") or []
        extracted, errors = registry.extract_field_rules(payload.sample, rules)
        result = {
            "parser_id": payload.parser_id,
            "matched": bool(extracted),
            "event": None,
            "extracted": extracted,
            "error": None,
            "reason": (
                "custom parsers are declarative field_rules, not code: this "
                "shows what the rules extract, not a normalized event"
            ),
        }
        if errors:
            result["errors"] = errors

    _record_test(client, payload.parser_id, result)
    return result


def _record_test(
    client: OpenSearch, parser_id: str, result: Dict[str, Any]
) -> None:
    """Remember the last test outcome on the parser itself."""
    try:
        client.update(
            index=PARSERS_INDEX,
            id=parser_id,
            body={"doc": {"last_test": {**result, "tested_at": _now()}}},
            refresh=REFRESH,
        )
    except Exception:
        # A failed bookkeeping write must not fail the test itself.
        pass


# ---------------------------------------------------------------------------
# HTTP surface
#
# Thin wrappers over the functions above. The `es` parameter exists so tests
# can call the implementation directly with a fake client; FastAPI would try
# to read an OpenSearch instance off the request, so the decorated handlers
# take only real request parameters and let the implementation resolve the
# client itself.
# ---------------------------------------------------------------------------


@router.get("/parsers")
def get_parsers_route(include_disabled: bool = False) -> Dict[str, Any]:
    # Wrapped in an envelope, matching GET /sources, so the client can grow
    # fields (counts, next_cursor) without a breaking shape change.
    return {"parsers": list_parsers(include_disabled=include_disabled)}


@router.get("/parsers/{parser_id}")
def get_parser_route(parser_id: str) -> Dict[str, Any]:
    record = get_parser(parser_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown parser {parser_id!r}")
    return record


@router.post("/parsers", status_code=201)
def create_parser_route(payload: ParserCreate) -> Dict[str, Any]:
    return create_parser(payload)


@router.put("/parsers/{parser_id}")
def update_parser_route(parser_id: str, payload: ParserUpdate) -> Dict[str, Any]:
    return update_parser(parser_id, payload)


@router.post("/parsers/{parser_id}/rollback")
def rollback_parser_route(parser_id: str) -> Dict[str, Any]:
    return rollback_parser(parser_id)


@router.delete("/parsers/{parser_id}")
def delete_parser_route(parser_id: str) -> Dict[str, Any]:
    return delete_parser(parser_id)


@router.post("/parsers/test")
def test_parser_route(payload: ParserTestRequest) -> Dict[str, Any]:
    return test_parser(payload)
