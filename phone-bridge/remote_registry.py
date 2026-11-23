"""Fixed, reviewed operation registry for Nexus Remote.

Governance rules (see docs/architecture/NEXUS_REMOTE_DESIGN.md §2):

* Operations are a static table in this module.  There is no dynamic import,
  plugin loading, or client-supplied executable/path/command/script.
* Every parameter has a closed type and bounds.  Unknown parameters are rejected.
* No operation may accept an executable, a command line, a filesystem path,
  a script body, or an unbounded argument vector.
* Unknown operation IDs fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

PERM_READ = "remote.read"
PERM_EXECUTE = "remote.execute"
PERM_SCRIPTS = "remote.scripts"
KNOWN_PERMISSIONS = frozenset({PERM_READ, PERM_EXECUTE, PERM_SCRIPTS})
# remote.scripts is deferred for 0.1 and can never be granted.
GRANTABLE_PERMISSIONS = frozenset({PERM_READ, PERM_EXECUTE})


class Risk(str, Enum):
    R0 = "R0"  # metadata only
    R1 = "R1"  # read-only diagnostics
    R2 = "R2"  # bounded, reversible action -> per-request approval
    R3 = "R3"  # destructive/disclosing -> per-request approval
    R4 = "R4"  # forbidden


APPROVAL_RISKS = frozenset({Risk.R2, Risk.R3})


class ParamError(ValueError):
    pass


class OperationError(RuntimeError):
    """Raised by an operation handler.  ``code`` is sent to the device."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Param:
    kind: type  # int | str | bool
    required: bool = False
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    max_len: int = 64

    def validate(self, name: str, value: Any) -> Any:
        if self.kind is bool:
            if not isinstance(value, bool):
                raise ParamError(f"{name} must be a boolean")
            return value
        if self.kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ParamError(f"{name} must be an integer")
            if self.minimum is not None and value < self.minimum:
                raise ParamError(f"{name} must be >= {self.minimum}")
            if self.maximum is not None and value > self.maximum:
                raise ParamError(f"{name} must be <= {self.maximum}")
            return value
        if self.kind is str:
            if not isinstance(value, str):
                raise ParamError(f"{name} must be a string")
            if len(value) > self.max_len:
                raise ParamError(f"{name} is too long")
            if self.choices and value not in self.choices:
                raise ParamError(f"{name} must be one of {', '.join(self.choices)}")
            return value
        raise ParamError(f"{name} has an unsupported schema type")  # pragma: no cover


Handler = Callable[[dict[str, Any], "OperationContext"], dict[str, Any]]


@dataclass(frozen=True)
class OperationContext:
    device_name: str = "linux"
    disk_paths: tuple[tuple[str, str], ...] = (("root", "/"),)
    proc_root: str = "/proc"
    max_output_bytes: int = 65536


@dataclass(frozen=True)
class Operation:
    id: str
    permission: str
    risk: Risk
    description: str
    handler: Handler
    params: dict[str, Param] = field(default_factory=dict)
    timeout: float = 5.0
    max_output_bytes: int = 16 * 1024
    enabled: bool = True

    @property
    def requires_approval(self) -> bool:
        return self.risk in APPROVAL_RISKS

    def validate_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        unknown = set(raw) - set(self.params)
        if unknown:
            raise ParamError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
        clean: dict[str, Any] = {}
        for name, spec in self.params.items():
            if name not in raw or raw[name] is None:
                if spec.required:
                    raise ParamError(f"{name} is required")
                if spec.default is not None:
                    clean[name] = spec.default
                continue
            clean[name] = spec.validate(name, raw[name])
        return clean

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "permission": self.permission,
            "risk": self.risk.value,
            "requires_approval": self.requires_approval,
            "description": self.description,
            "timeout": self.timeout,
            "max_output_bytes": self.max_output_bytes,
            "params": {
                name: {
                    "type": spec.kind.__name__,
                    "required": spec.required,
                    **({"default": spec.default} if spec.default is not None else {}),
                    **({"min": spec.minimum} if spec.minimum is not None else {}),
                    **({"max": spec.maximum} if spec.maximum is not None else {}),
                    **({"choices": list(spec.choices)} if spec.choices else {}),
                }
                for name, spec in self.params.items()
            },
        }


class RegistryError(LookupError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OperationRegistry:
    def __init__(self, operations: list[Operation]) -> None:
        self._ops: dict[str, Operation] = {}
        for op in operations:
            self._admit(op)

    def _admit(self, op: Operation) -> None:
        if op.id in self._ops:
            raise ValueError(f"duplicate operation id {op.id}")
        if op.permission not in KNOWN_PERMISSIONS:
            raise ValueError(f"{op.id}: unknown permission {op.permission}")
        if op.risk is Risk.R4:
            raise ValueError(f"{op.id}: R4 operations are forbidden")
        if op.risk in (Risk.R0, Risk.R1) and op.permission != PERM_READ:
            raise ValueError(f"{op.id}: read operations must use {PERM_READ}")
        if op.risk in APPROVAL_RISKS and op.permission == PERM_READ:
            raise ValueError(f"{op.id}: mutating operations cannot use {PERM_READ}")
        if op.timeout <= 0 or op.timeout > 30:
            raise ValueError(f"{op.id}: timeout must be in (0, 30]")
        if op.max_output_bytes <= 0 or op.max_output_bytes > 256 * 1024:
            raise ValueError(f"{op.id}: output cap must be in (0, 256 KiB]")
        for name, spec in op.params.items():
            if spec.kind not in (int, str, bool):
                raise ValueError(f"{op.id}.{name}: only int/str/bool parameters are allowed")
            if spec.kind is str and not spec.choices:
                # Anti-generic-wrapper rule: no free-form strings in 0.1.
                raise ValueError(f"{op.id}.{name}: string parameters must be enumerated")
        self._ops[op.id] = op

    def get(self, op_id: str) -> Operation:
        op = self._ops.get(op_id)
        if op is None:
            raise RegistryError("unknown_operation", f"unknown operation {op_id!r}")
        if not op.enabled:
            raise RegistryError("operation_disabled", f"operation {op_id!r} is disabled")
        return op

    def all(self) -> list[Operation]:
        return [self._ops[k] for k in sorted(self._ops)]

    def available_for(self, permissions: set[str]) -> list[Operation]:
        return [op for op in self.all() if op.enabled and op.permission in permissions]


def default_registry() -> OperationRegistry:
    import remote_ops

    return OperationRegistry(
        [
            Operation(
                id="system.status",
                permission=PERM_READ,
                risk=Risk.R1,
                description="CPU, memory, disk usage, load and uptime of the Linux host",
                handler=remote_ops.system_status,
                timeout=3.0,
                max_output_bytes=16 * 1024,
            ),
            Operation(
                id="process.list",
                permission=PERM_READ,
                risk=Risk.R1,
                description="Bounded, sorted list of processes (no command lines or environment)",
                handler=remote_ops.process_list,
                params={
                    "limit": Param(int, default=50, minimum=1, maximum=200),
                    "offset": Param(int, default=0, minimum=0, maximum=100_000),
                    "sort_by": Param(
                        str, default="memory", choices=("memory", "cpu_time", "pid", "name")
                    ),
                    "order": Param(str, default="desc", choices=("asc", "desc")),
                    "own_only": Param(bool, default=False),
                },
                timeout=5.0,
                max_output_bytes=64 * 1024,
            ),
            Operation(
                id="process.inspect",
                permission=PERM_READ,
                risk=Risk.R1,
                description="Minimal metadata for one process, with PID-reuse protection",
                handler=remote_ops.process_inspect,
                params={
                    "pid": Param(int, required=True, minimum=1, maximum=4_194_304),
                    "start_ticks": Param(int, minimum=0, maximum=2**62),
                },
                timeout=3.0,
                max_output_bytes=8 * 1024,
            ),
        ]
    )
