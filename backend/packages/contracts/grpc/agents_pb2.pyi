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
