from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Resolution(_message.Message):
    __slots__ = ("request_id", "run_id", "raised_at_tick", "owning_item", "checkpoint_id", "permitted_options", "rules_ver")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    RAISED_AT_TICK_FIELD_NUMBER: _ClassVar[int]
    OWNING_ITEM_FIELD_NUMBER: _ClassVar[int]
    CHECKPOINT_ID_FIELD_NUMBER: _ClassVar[int]
    PERMITTED_OPTIONS_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    run_id: str
    raised_at_tick: int
    owning_item: str
    checkpoint_id: str
    permitted_options: _containers.RepeatedScalarFieldContainer[str]
    rules_ver: str
    def __init__(self, request_id: _Optional[str] = ..., run_id: _Optional[str] = ..., raised_at_tick: _Optional[int] = ..., owning_item: _Optional[str] = ..., checkpoint_id: _Optional[str] = ..., permitted_options: _Optional[_Iterable[str]] = ..., rules_ver: _Optional[str] = ...) -> None: ...

class Decision(_message.Message):
    __slots__ = ("request_id", "chosen_option", "resolver", "rules_ver", "provenance")
    class ProvenanceEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    CHOSEN_OPTION_FIELD_NUMBER: _ClassVar[int]
    RESOLVER_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    chosen_option: str
    resolver: ResolverIdentity
    rules_ver: str
    provenance: _containers.ScalarMap[str, str]
    def __init__(self, request_id: _Optional[str] = ..., chosen_option: _Optional[str] = ..., resolver: _Optional[_Union[ResolverIdentity, _Mapping]] = ..., rules_ver: _Optional[str] = ..., provenance: _Optional[_Mapping[str, str]] = ...) -> None: ...

class StatementRequest(_message.Message):
    __slots__ = ("request_id", "run_id", "raised_at_tick", "owning_item", "cp_index", "person", "scope", "rules_ver")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    RAISED_AT_TICK_FIELD_NUMBER: _ClassVar[int]
    OWNING_ITEM_FIELD_NUMBER: _ClassVar[int]
    CP_INDEX_FIELD_NUMBER: _ClassVar[int]
    PERSON_FIELD_NUMBER: _ClassVar[int]
    SCOPE_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    run_id: str
    raised_at_tick: int
    owning_item: str
    cp_index: int
    person: str
    scope: AuthorizedScope
    rules_ver: str
    def __init__(self, request_id: _Optional[str] = ..., run_id: _Optional[str] = ..., raised_at_tick: _Optional[int] = ..., owning_item: _Optional[str] = ..., cp_index: _Optional[int] = ..., person: _Optional[str] = ..., scope: _Optional[_Union[AuthorizedScope, _Mapping]] = ..., rules_ver: _Optional[str] = ...) -> None: ...

class AuthorizedScope(_message.Message):
    __slots__ = ("people", "items")
    PEOPLE_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    people: _containers.RepeatedScalarFieldContainer[str]
    items: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, people: _Optional[_Iterable[str]] = ..., items: _Optional[_Iterable[str]] = ...) -> None: ...

class Statement(_message.Message):
    __slots__ = ("request_id", "briefing", "objection", "citations", "context", "producer", "rules_ver")
    REQUEST_ID_FIELD_NUMBER: _ClassVar[int]
    BRIEFING_FIELD_NUMBER: _ClassVar[int]
    OBJECTION_FIELD_NUMBER: _ClassVar[int]
    CITATIONS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    PRODUCER_FIELD_NUMBER: _ClassVar[int]
    RULES_VER_FIELD_NUMBER: _ClassVar[int]
    request_id: str
    briefing: str
    objection: str
    citations: _containers.RepeatedScalarFieldContainer[int]
    context: RetrievedContext
    producer: StatementProducer
    rules_ver: str
    def __init__(self, request_id: _Optional[str] = ..., briefing: _Optional[str] = ..., objection: _Optional[str] = ..., citations: _Optional[_Iterable[int]] = ..., context: _Optional[_Union[RetrievedContext, _Mapping]] = ..., producer: _Optional[_Union[StatementProducer, _Mapping]] = ..., rules_ver: _Optional[str] = ...) -> None: ...

class StatementProducer(_message.Message):
    __slots__ = ("person", "kind", "model_identity")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[StatementProducer.Kind]
        MODEL: _ClassVar[StatementProducer.Kind]
        SCRIPTED: _ClassVar[StatementProducer.Kind]
    KIND_UNSPECIFIED: StatementProducer.Kind
    MODEL: StatementProducer.Kind
    SCRIPTED: StatementProducer.Kind
    PERSON_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    MODEL_IDENTITY_FIELD_NUMBER: _ClassVar[int]
    person: str
    kind: StatementProducer.Kind
    model_identity: str
    def __init__(self, person: _Optional[str] = ..., kind: _Optional[_Union[StatementProducer.Kind, str]] = ..., model_identity: _Optional[str] = ...) -> None: ...

class RetrievedContext(_message.Message):
    __slots__ = ("director", "line", "since_seq", "through_seq", "events", "draw", "unlocking_note")
    class DrawEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: int
        def __init__(self, key: _Optional[str] = ..., value: _Optional[int] = ...) -> None: ...
    DIRECTOR_FIELD_NUMBER: _ClassVar[int]
    LINE_FIELD_NUMBER: _ClassVar[int]
    SINCE_SEQ_FIELD_NUMBER: _ClassVar[int]
    THROUGH_SEQ_FIELD_NUMBER: _ClassVar[int]
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    DRAW_FIELD_NUMBER: _ClassVar[int]
    UNLOCKING_NOTE_FIELD_NUMBER: _ClassVar[int]
    director: str
    line: _containers.RepeatedScalarFieldContainer[str]
    since_seq: int
    through_seq: int
    events: _containers.RepeatedCompositeFieldContainer[RetrievedEvent]
    draw: _containers.ScalarMap[str, int]
    unlocking_note: str
    def __init__(self, director: _Optional[str] = ..., line: _Optional[_Iterable[str]] = ..., since_seq: _Optional[int] = ..., through_seq: _Optional[int] = ..., events: _Optional[_Iterable[_Union[RetrievedEvent, _Mapping]]] = ..., draw: _Optional[_Mapping[str, int]] = ..., unlocking_note: _Optional[str] = ...) -> None: ...

class RetrievedEvent(_message.Message):
    __slots__ = ("seq", "tick", "kind", "person", "item", "detail")
    SEQ_FIELD_NUMBER: _ClassVar[int]
    TICK_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PERSON_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    seq: int
    tick: int
    kind: str
    person: str
    item: str
    detail: str
    def __init__(self, seq: _Optional[int] = ..., tick: _Optional[int] = ..., kind: _Optional[str] = ..., person: _Optional[str] = ..., item: _Optional[str] = ..., detail: _Optional[str] = ...) -> None: ...

class ResolverIdentity(_message.Message):
    __slots__ = ("kind", "identity")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        KIND_UNSPECIFIED: _ClassVar[ResolverIdentity.Kind]
        HUMAN_IN_PERSON: _ClassVar[ResolverIdentity.Kind]
        HUMAN_FROM_TRAY: _ClassVar[ResolverIdentity.Kind]
        AGENT: _ClassVar[ResolverIdentity.Kind]
        STUB: _ClassVar[ResolverIdentity.Kind]
    KIND_UNSPECIFIED: ResolverIdentity.Kind
    HUMAN_IN_PERSON: ResolverIdentity.Kind
    HUMAN_FROM_TRAY: ResolverIdentity.Kind
    AGENT: ResolverIdentity.Kind
    STUB: ResolverIdentity.Kind
    KIND_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_FIELD_NUMBER: _ClassVar[int]
    kind: ResolverIdentity.Kind
    identity: str
    def __init__(self, kind: _Optional[_Union[ResolverIdentity.Kind, str]] = ..., identity: _Optional[str] = ...) -> None: ...
