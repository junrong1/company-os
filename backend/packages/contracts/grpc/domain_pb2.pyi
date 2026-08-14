from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class Consultation(_message.Message):
    __slots__ = ("request_id", "run_id", "raised_at_tick", "period_index", "owning_item", "metrics", "rules_ver")
    class MetricsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    RAISED_AT_TICK_FIELD_NUMBER: _ClassVar[int]
    PERIOD_INDEX_FIELD_NUMBER: _ClassVar[int]
    OWNING_ITEM_FIELD_NUMBER: _ClassVar[int]
    METRICS_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    run_id: str
    raised_at_tick: int
    period_index: int
    owning_item: str
    metrics: _containers.ScalarMap[str, int]
    rules_ver: str
    def __init__(self, request_id: _Optional[str] = ..., run_id: _Optional[str] = ..., raised_at_tick: _Optional[int] = ..., period_index: _Optional[int] = ..., owning_item: _Optional[str] = ..., metrics: _Optional[_Mapping[str, int]] = ..., rules_ver: _Optional[str] = ...) -> None: ...

class Effect(_message.Message):
    __slots__ = ("request_id", "metric_deltas", "rules_ver", "model_identity")
    class MetricDeltasEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    METRIC_DELTAS_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    MODEL_IDENTITY_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    metric_deltas: _containers.ScalarMap[str, int]
    rules_ver: str
    model_identity: str
    def __init__(self, request_id: _Optional[str] = ..., metric_deltas: _Optional[_Mapping[str, int]] = ..., rules_ver: _Optional[str] = ..., model_identity: _Optional[str] = ...) -> None: ...
