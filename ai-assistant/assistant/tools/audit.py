import json
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def log(self, event: Mapping[str, Any]) -> bool:
        if not self.enabled:
            return True
        if not isinstance(event, Mapping):
            self.last_error = "Audit event must be a mapping."
            return False

        payload = dict(event)
        payload.setdefault(
            "timestamp", datetime.now(timezone.utc).isoformat()
        )

        try:
            line = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            line.encode("utf-8")
        except UnicodeEncodeError:
            try:
                line = json.dumps(
                    payload,
                    ensure_ascii=True,
                    sort_keys=True,
                    default=str,
                )
            except (TypeError, ValueError) as exc:
                self.last_error = (
                    f"Could not serialize audit event: {exc}"
                )
                return False
        except (TypeError, ValueError) as exc:
            self.last_error = f"Could not serialize audit event: {exc}"
            return False

        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open(
                    "a", encoding="utf-8", newline="\n"
                ) as handle:
                    handle.write(line + "\n")
                    handle.flush()
        except (OSError, UnicodeError) as exc:
            self.last_error = f"Could not append audit event: {exc}"
            return False

        self.last_error = None
        return True


__all__ = ["AuditLogger"]
