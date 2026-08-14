#!/usr/bin/env python
"""Generate the gRPC stubs from `proto/` into `packages/contracts/grpc/`.

Run from the backend directory:

    uv run python scripts/generate_protos.py

Why the import rewrite exists. `protoc` emits flat, top-level imports — a generated
`kernel_pb2.py` contains `import events_pb2`, which only resolves if the output
directory itself is on `sys.path`. Putting the stubs inside a package and leaving
that import alone would work in a test that happens to have added the directory to
the path, and fail in a service. Rewriting the imports to be package-qualified is
the standard fix; doing it here, in one visible place, is better than a `sys.path`
manipulation hidden in `contracts/grpc/__init__.py`.

The alternative — nesting the `.proto` files under a directory mirroring the Python
package so `protoc` emits qualified imports itself — was not taken because the plan's
per-unit file lists put the protos at `backend/proto/*.proto`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROTO_DIR = BACKEND / "proto"
OUT_DIR = BACKEND / "packages" / "contracts" / "grpc"

PACKAGE = "contracts.grpc"

HEADER = '''"""Generated gRPC stubs. Do not edit.

Regenerate with `uv run python scripts/generate_protos.py` from `backend/`.

Only services import this subpackage. `simcore` must not: it pulls in `grpc`, and
R5 forbids the kernel library from taking a transport dependency.
"""
'''


def _proto_files() -> list[Path]:
    files = sorted(PROTO_DIR.glob("*.proto"))
    if not files:
        raise SystemExit(f"no .proto files in {PROTO_DIR}")
    return files


def _generate(files: list[Path]) -> None:
    from grpc_tools import protoc

    args = [
        "protoc",
        f"--proto_path={PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--pyi_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        *[str(path) for path in files],
    ]

    code = protoc.main(args)
    if code != 0:
        raise SystemExit(f"protoc failed with exit code {code}")


def _rewrite_imports(module_names: set[str]) -> list[str]:
    """Make generated cross-imports package-qualified. Returns the files changed."""
    changed: list[str] = []

    for path in sorted(OUT_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue

        original = path.read_text()
        text = original

        for name in module_names:
            # `import events_pb2 as events__pb2`  ->  `from contracts.grpc import ...`
            text = re.sub(
                rf"^import {name} as (\w+)$",
                rf"from {PACKAGE} import {name} as \1",
                text,
                flags=re.MULTILINE,
            )
            # A bare `import events_pb2` on its own line.
            text = re.sub(
                rf"^import {name}$",
                rf"from {PACKAGE} import {name}",
                text,
                flags=re.MULTILINE,
            )

        if text != original:
            path.write_text(text)
            changed.append(path.name)

    # The .pyi stubs carry the same flat imports.
    for path in sorted(OUT_DIR.glob("*.pyi")):
        original = path.read_text()
        text = original
        for name in module_names:
            text = re.sub(
                rf"^import {name}$", rf"from {PACKAGE} import {name}", text, flags=re.MULTILINE
            )
        if text != original:
            path.write_text(text)
            changed.append(path.name)

    return changed


def main() -> int:
    files = _proto_files()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _generate(files)

    module_names = {f"{path.stem}_pb2" for path in files}
    changed = _rewrite_imports(module_names)

    (OUT_DIR / "__init__.py").write_text(HEADER)

    produced = sorted(p.name for p in OUT_DIR.iterdir() if p.name != "__pycache__")
    print(f"generated {len(produced)} files in {OUT_DIR.relative_to(BACKEND)}:")
    for name in produced:
        print(f"  {name}")
    if changed:
        print(f"rewrote flat imports in: {', '.join(sorted(set(changed)))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
