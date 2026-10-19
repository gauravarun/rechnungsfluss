"""Render Terraform ``templatefile`` templates in tests (``${var}`` and the ``$${`` escape)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PIPELINE_MODULE = REPO / "infra" / "terraform" / "modules" / "pipeline"
_SENTINEL = "\x00ESCAPED\x00"
_VAR = re.compile(r"\$\{([a-z_]+)\}")


def render(path: Path, variables: dict[str, object]) -> Any:
    text = path.read_text("utf-8").replace("$${", _SENTINEL)

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(f"template variable {name!r} not provided")
        return str(variables[name])

    rendered = _VAR.sub(sub, text).replace(_SENTINEL, "${")
    return json.loads(rendered)
