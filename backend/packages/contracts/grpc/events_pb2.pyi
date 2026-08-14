from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class EventKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    EVENT_KIND_UNSPECIFIED: _ClassVar[EventKind]
    GENESIS: _ClassVar[EventKind]
    DAY_CHECKPOINT: _ClassVar[EventKind]
    RATE_CHANGED: _ClassVar[EventKind]
    RUN_TERMINATED: _ClassVar[EventKind]
    RUN_FORKED: _ClassVar[EventKind]
    STORE_RECOVERED: _ClassVar[EventKind]
    REQUEST_RAISED: _ClassVar[EventKind]
    INPUT_RECEIVED: _ClassVar[EventKind]
    ANSWER_REJECTED: _ClassVar[EventKind]
    CEO_INPUT: _ClassVar[EventKind]
    CHECKPOINT_RAISED: _ClassVar[EventKind]
    DECISION_RESOLVED: _ClassVar[EventKind]
    WORK_ASSIGNED: _ClassVar[EventKind]
    WORK_REASSIGNED: _ClassVar[EventKind]
    WORK_RETURNED_TO_BACKLOG: _ClassVar[EventKind]
    DELIVERABLE_PRODUCED: _ClassVar[EventKind]
    ITEM_UNLOCKED: _ClassVar[EventKind]
    METRICS_APPLIED: _ClassVar[EventKind]
    LOAD_CHANGED: _ClassVar[EventKind]
    HIRE_REQUESTED: _ClassVar[EventKind]
    HIRE_ARRIVED: _ClassVar[EventKind]
    HIRE_REFUSED: _ClassVar[EventKind]
    ATTRITION: _ClassVar[EventKind]
    DAILY_COSTS_APPLIED: _ClassVar[EventKind]
    COMMAND_REJECTED: _ClassVar[EventKind]
EVENT_KIND_UNSPECIFIED: EventKind
GENESIS: EventKind
DAY_CHECKPOINT: EventKind
RATE_CHANGED: EventKind
RUN_TERMINATED: EventKind
RUN_FORKED: EventKind
STORE_RECOVERED: EventKind
REQUEST_RAISED: EventKind
INPUT_RECEIVED: EventKind
ANSWER_REJECTED: EventKind
CEO_INPUT: EventKind
CHECKPOINT_RAISED: EventKind
DECISION_RESOLVED: EventKind
WORK_ASSIGNED: EventKind
WORK_REASSIGNED: EventKind
WORK_RETURNED_TO_BACKLOG: EventKind
DELIVERABLE_PRODUCED: EventKind
ITEM_UNLOCKED: EventKind
METRICS_APPLIED: EventKind
LOAD_CHANGED: EventKind
HIRE_REQUESTED: EventKind
HIRE_ARRIVED: EventKind
HIRE_REFUSED: EventKind
ATTRITION: EventKind
DAILY_COSTS_APPLIED: EventKind
COMMAND_REJECTED: EventKind

class Envelope(_message.Message):
    __slots__ = ("seq", "tick", "kind", "schema_ver", "rules_ver", "payload", "run_id", "command_id", "request_id", "ingested_at")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    TICK_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_VER_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    INGESTED_AT_FIELD_NUMBER: _ClassVar[int]
    seq: int
    tick: int
    kind: EventKind
    schema_ver: int
    rules_ver: str
    payload: bytes
    run_id: str
    command_id: str
    request_id: str
    ingested_at: str
    def __init__(self, seq: _Optional[int] = ..., tick: _Optional[int] = ..., kind: _Optional[_Union[EventKind, str]] = ..., schema_ver: _Optional[int] = ..., rules_ver: _Optional[str] = ..., payload: _Optional[bytes] = ..., run_id: _Optional[str] = ..., command_id: _Optional[str] = ..., request_id: _Optional[str] = ..., ingested_at: _Optional[str] = ...) -> None: ...
