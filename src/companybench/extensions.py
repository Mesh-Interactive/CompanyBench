"""Import explicitly trusted Python factories shared by extension interfaces."""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable
from hashlib import sha256
from importlib import import_module, util
from pathlib import Path
from typing import Any


def implementation_identity(value: Any) -> dict[str, Any]:
    """Describe implementation code for cache invalidation, without storing source."""
    target = value if inspect.isfunction(value) or inspect.isclass(value) else type(value)
    module = sys.modules.get(target.__module__)
    source_hash = getattr(module, "__companybench_source_hash__", None)
    if source_hash is None and module is not None:
        path = getattr(module, "__file__", None)
        if path and Path(path).is_file():
            source_hash = sha256(Path(path).read_bytes()).hexdigest()
            # Keep the identity of the loaded code stable if its file later changes.
            module.__dict__["__companybench_source_hash__"] = source_hash
    return {
        "module": target.__module__,
        "qualname": target.__qualname__,
        "source_hash": source_hash,
    }


def load_factory(reference: str) -> Callable[..., Any]:
    """Resolve ``module:factory`` or ``/path/file.py:factory`` without invoking it.

    This executes trusted Python code. File modules are identified by both their
    absolute path and source bytes, and registered before execution so dataclasses
    and postponed type annotations can resolve their defining module.
    """
    module_name, separator, symbol = reference.rpartition(":")
    if not separator or not symbol.isidentifier() or not module_name:
        raise ValueError("An extension must be module:factory or /path/file.py:factory")
    if module_name.endswith(".py"):
        path = Path(module_name).expanduser().resolve(strict=True)
        source = path.read_bytes()
        name = "companybench_extension_" + sha256(str(path).encode() + b"\0" + source).hexdigest()
        module = sys.modules.get(name)
        if module is None:
            spec = util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Cannot import extension file: {path}")
            module = util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                # Execute the exact hashed bytes, avoiding stale timestamp-based .pyc caches.
                exec(compile(source, str(path), "exec"), module.__dict__)
                module.__dict__["__companybench_source_hash__"] = sha256(source).hexdigest()
            except BaseException:
                sys.modules.pop(name, None)
                raise
    else:
        if not all(part.isidentifier() for part in module_name.split(".")):
            raise ValueError(f"Invalid extension module: {module_name!r}")
        module = import_module(module_name)
    factory = getattr(module, symbol)
    if not callable(factory):
        raise TypeError(f"Extension factory {reference!r} is not callable")
    # Functions/classes support metadata; callable extension objects may use slots.
    try:
        factory.__companybench_source_identity__ = implementation_identity(factory)
    except (AttributeError, TypeError):
        pass
    return factory
