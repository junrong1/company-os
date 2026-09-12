import events_pb2 as _events_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class CommandKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COMMAND_KIND_UNSPECIFIED: _ClassVar[CommandKind]
    START_RUN: _ClassVar[CommandKind]
    FORK_RUN: _ClassVar[CommandKind]
    ASSIGN_WORK: _ClassVar[CommandKind]
    REASSIGN_WORK: _ClassVar[CommandKind]
    RETURN_TO_BACKLOG: _ClassVar[CommandKind]
    RESOLVE_CHECKPOINT: _ClassVar[CommandKind]
    SUBMIT_CEO_INPUT: _ClassVar[CommandKind]
    SET_RATE: _ClassVar[CommandKind]
    REQUEST_HIRE: _ClassVar[CommandKind]
    ASK_PERSON: _ClassVar[CommandKind]
    COMPARE_OPTIONS: _ClassVar[CommandKind]
    DECIDE_AUTHORIZATION: _ClassVar[CommandKind]
COMMAND_KIND_UNSPECIFIED: CommandKind
START_RUN: CommandKind
FORK_RUN: CommandKind
ASSIGN_WORK: CommandKind
REASSIGN_WORK: CommandKind
RETURN_TO_BACKLOG: CommandKind
RESOLVE_CHECKPOINT: CommandKind
SUBMIT_CEO_INPUT: CommandKind
SET_RATE: CommandKind
REQUEST_HIRE: CommandKind
ASK_PERSON: CommandKind
COMPARE_OPTIONS: CommandKind
DECIDE_AUTHORIZATION: CommandKind

class Command(_message.Message):
    __slots__ = ("run_id", "idempotency_key", "command_id", "kind", "payload")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    idempotency_key: str
    command_id: str
    kind: CommandKind
    payload: bytes
    def __init__(self, run_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ..., command_id: _Optional[str] = ..., kind: _Optional[_Union[CommandKind, str]] = ..., payload: _Optional[bytes] = ...) -> None: ...

class CommandOutcome(_message.Message):
    __slots__ = ("status", "applied_tick", "produced_seq", "reason")
    class Status(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        STATUS_UNSPECIFIED: _ClassVar[CommandOutcome.Status]
        APPLIED: _ClassVar[CommandOutcome.Status]
        REJECTED: _ClassVar[CommandOutcome.Status]
        DUPLICATE: _ClassVar[CommandOutcome.Status]
        QUEUED_RUN_PAUSED: _ClassVar[CommandOutcome.Status]
        RUN_TERMINATED: _ClassVar[CommandOutcome.Status]
        RUN_NOT_FOUND: _ClassVar[CommandOutcome.Status]
    STATUS_UNSPECIFIED: CommandOutcome.Status
    APPLIED: CommandOutcome.Status
    REJECTED: CommandOutcome.Status
    DUPLICATE: CommandOutcome.Status
    QUEUED_RUN_PAUSED: CommandOutcome.Status
    RUN_TERMINATED: CommandOutcome.Status
    RUN_NOT_FOUND: CommandOutcome.Status
    STATUS_FIELD_NUMBER: _ClassVar[int]
    APPLIED_TICK_FIELD_NUMBER: _ClassVar[int]
    PRODUCED_SEQ_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    status: CommandOutcome.Status
    applied_tick: int
    produced_seq: _containers.RepeatedScalarFieldContainer[int]
    reason: str
    def __init__(self, status: _Optional[_Union[CommandOutcome.Status, str]] = ..., applied_tick: _Optional[int] = ..., produced_seq: _Optional[_Iterable[int]] = ..., reason: _Optional[str] = ...) -> None: ...

class OutcomeByKeyRequest(_message.Message):
    __slots__ = ("run_id", "idempotency_key")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    idempotency_key: str
    def __init__(self, run_id: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class SubscribeRequest(_message.Message):
    __slots__ = ("run_id", "after_seq")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    AFTER_SEQ_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    after_seq: int
    def __init__(self, run_id: _Optional[str] = ..., after_seq: _Optional[int] = ...) -> None: ...

class FoldRequest(_message.Message):
    __slots__ = ("run_id", "through_seq", "at_live_head", "allow_snapshot")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQ_FIELD_NUMBER: _ClassVar[int]
    AT_LIVE_HEAD_FIELD_NUMBER: _ClassVar[int]
    ALLOW_SNAPSHOT_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    through_seq: int
    at_live_head: bool
    allow_snapshot: bool
    def __init__(self, run_id: _Optional[str] = ..., through_seq: _Optional[int] = ..., at_live_head: bool = ..., allow_snapshot: bool = ...) -> None: ...

class FoldResponse(_message.Message):
    __slots__ = ("through_seq", "tick", "rules_ver", "state_hash", "subsystem_hashes", "state_shape_ver", "state")
    class SubsystemHashesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    THROUGH_SEQ_FIELD_NUMBER: _ClassVar[int]
    TICK_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    STATE_HASH_FIELD_NUMBER: _ClassVar[int]
    SUBSYSTEM_HASHES_FIELD_NUMBER: _ClassVar[int]
    STATE_SHAPE_VER_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    through_seq: int
    tick: int
    rules_ver: str
    state_hash: str
    subsystem_hashes: _containers.ScalarMap[str, str]
    state_shape_ver: int
    state: bytes
    def __init__(self, through_seq: _Optional[int] = ..., tick: _Optional[int] = ..., rules_ver: _Optional[str] = ..., state_hash: _Optional[str] = ..., subsystem_hashes: _Optional[_Mapping[str, str]] = ..., state_shape_ver: _Optional[int] = ..., state: _Optional[bytes] = ...) -> None: ...

class ReplayRequest(_message.Message):
    __slots__ = ("run_id", "through_seq")
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQ_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    through_seq: int
    def __init__(self, run_id: _Optional[str] = ..., through_seq: _Optional[int] = ...) -> None: ...

class ReplayResponse(_message.Message):
    __slots__ = ("matched", "state_hash", "refusal", "diverged_at_seq", "diverged_at_tick")
    MATCHED_FIELD_NUMBER: _ClassVar[int]
    STATE_HASH_FIELD_NUMBER: _ClassVar[int]
    REFUSAL_FIELD_NUMBER: _ClassVar[int]
    DIVERGED_AT_SEQ_FIELD_NUMBER: _ClassVar[int]
    DIVERGED_AT_TICK_FIELD_NUMBER: _ClassVar[int]
    matched: bool
    state_hash: str
    refusal: str
    diverged_at_seq: int
    diverged_at_tick: int
    def __init__(self, matched: bool = ..., state_hash: _Optional[str] = ..., refusal: _Optional[str] = ..., diverged_at_seq: _Optional[int] = ..., diverged_at_tick: _Optional[int] = ...) -> None: ...

class ForkRequest(_message.Message):
    __slots__ = ("parent_run_id", "at_seq", "option_index", "idempotency_key")
    PARENT_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    AT_SEQ_FIELD_NUMBER: _ClassVar[int]
    OPTION_INDEX_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    parent_run_id: str
    at_seq: int
    option_index: int
    idempotency_key: str
    def __init__(self, parent_run_id: _Optional[str] = ..., at_seq: _Optional[int] = ..., option_index: _Optional[int] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class ForkResponse(_message.Message):
    __slots__ = ("child_run_id", "copied_through_seq", "refusal", "forked_at_tick", "lineage_root_id", "item", "cp_index", "option_index", "parent_option_index", "created")
    CHILD_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    COPIED_THROUGH_SEQ_FIELD_NUMBER: _ClassVar[int]
    REFUSAL_FIELD_NUMBER: _ClassVar[int]
    FORKED_AT_TICK_FIELD_NUMBER: _ClassVar[int]
    LINEAGE_ROOT_ID_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    CP_INDEX_FIELD_NUMBER: _ClassVar[int]
    OPTION_INDEX_FIELD_NUMBER: _ClassVar[int]
    PARENT_OPTION_INDEX_FIELD_NUMBER: _ClassVar[int]
    CREATED_FIELD_NUMBER: _ClassVar[int]
    child_run_id: str
    copied_through_seq: int
    refusal: str
    forked_at_tick: int
    lineage_root_id: str
    item: str
    cp_index: int
    option_index: int
    parent_option_index: int
    created: bool
    def __init__(self, child_run_id: _Optional[str] = ..., copied_through_seq: _Optional[int] = ..., refusal: _Optional[str] = ..., forked_at_tick: _Optional[int] = ..., lineage_root_id: _Optional[str] = ..., item: _Optional[str] = ..., cp_index: _Optional[int] = ..., option_index: _Optional[int] = ..., parent_option_index: _Optional[int] = ..., created: bool = ...) -> None: ...

class ExportRequest(_message.Message):
    __slots__ = ("run_id",)
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    def __init__(self, run_id: _Optional[str] = ...) -> None: ...

class ExportResponse(_message.Message):
    __slots__ = ("artifact", "state_hash", "rules_ver", "envelope_schema_ver")
    ARTIFACT_FIELD_NUMBER: _ClassVar[int]
    STATE_HASH_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_SCHEMA_VER_FIELD_NUMBER: _ClassVar[int]
    artifact: bytes
    state_hash: str
    rules_ver: str
    envelope_schema_ver: int
    def __init__(self, artifact: _Optional[bytes] = ..., state_hash: _Optional[str] = ..., rules_ver: _Optional[str] = ..., envelope_schema_ver: _Optional[int] = ...) -> None: ...

class DiagnoseRequest(_message.Message):
    __slots__ = ("run_id",)
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    run_id: str
    def __init__(self, run_id: _Optional[str] = ...) -> None: ...

class DiagnoseResponse(_message.Message):
    __slots__ = ("rate", "rate_effective_tick", "subscribers", "tick_task_state", "tick_task_exception", "last_wake_at", "sim_time_lag_ticks", "achieved_multiplier", "unresolved_checkpoints", "outstanding_requests", "store_reachable", "lease_held")
    RATE_FIELD_NUMBER: _ClassVar[int]
    RATE_EFFECTIVE_TICK_FIELD_NUMBER: _ClassVar[int]
    SUBSCRIBERS_FIELD_NUMBER: _ClassVar[int]
    TICK_TASK_STATE_FIELD_NUMBER: _ClassVar[int]
    TICK_TASK_EXCEPTION_FIELD_NUMBER: _ClassVar[int]
    LAST_WAKE_AT_FIELD_NUMBER: _ClassVar[int]
    SIM_TIME_LAG_TICKS_FIELD_NUMBER: _ClassVar[int]
    ACHIEVED_MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    UNRESOLVED_CHECKPOINTS_FIELD_NUMBER: _ClassVar[int]
    OUTSTANDING_REQUESTS_FIELD_NUMBER: _ClassVar[int]
    STORE_REACHABLE_FIELD_NUMBER: _ClassVar[int]
    LEASE_HELD_FIELD_NUMBER: _ClassVar[int]
    rate: int
    rate_effective_tick: int
    subscribers: int
    tick_task_state: str
    tick_task_exception: str
    last_wake_at: str
    sim_time_lag_ticks: int
    achieved_multiplier: float
    unresolved_checkpoints: _containers.RepeatedScalarFieldContainer[str]
    outstanding_requests: _containers.RepeatedCompositeFieldContainer[OutstandingRequest]
    store_reachable: bool
    lease_held: bool
    def __init__(self, rate: _Optional[int] = ..., rate_effective_tick: _Optional[int] = ..., subscribers: _Optional[int] = ..., tick_task_state: _Optional[str] = ..., tick_task_exception: _Optional[str] = ..., last_wake_at: _Optional[str] = ..., sim_time_lag_ticks: _Optional[int] = ..., achieved_multiplier: _Optional[float] = ..., unresolved_checkpoints: _Optional[_Iterable[str]] = ..., outstanding_requests: _Optional[_Iterable[_Union[OutstandingRequest, _Mapping]]] = ..., store_reachable: bool = ..., lease_held: bool = ...) -> None: ...

class OutstandingRequest(_message.Message):
    __slots__ = ("request_id", "owning_item", "raised_at_tick", "age_ticks", "service")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    OWNING_ITEM_FIELD_NUMBER: _ClassVar[int]
    RAISED_AT_TICK_FIELD_NUMBER: _ClassVar[int]
    AGE_TICKS_FIELD_NUMBER: _ClassVar[int]
    SERVICE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    owning_item: str
    raised_at_tick: int
    age_ticks: int
    service: str
    def __init__(self, request_id: _Optional[str] = ..., owning_item: _Optional[str] = ..., raised_at_tick: _Optional[int] = ..., age_ticks: _Optional[int] = ..., service: _Optional[str] = ...) -> None: ...
