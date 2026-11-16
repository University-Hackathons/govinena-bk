"""SQL-backed LangGraph saver; binary serializer output is JSON/base64, never pickle."""

import base64
from datetime import UTC, datetime

from langgraph.checkpoint.memory import InMemorySaver

from app.models.agent_run import AgentRun


def pack(value):
    if isinstance(value, bytes):
        return {"bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"tuple": [pack(item) for item in value]}
    if isinstance(value, dict):
        return {"map": [[pack(key), pack(item)] for key, item in value.items()]}
    if isinstance(value, list):
        return [pack(item) for item in value]
    return value


def unpack(value):
    if isinstance(value, list):
        return [unpack(item) for item in value]
    if isinstance(value, dict):
        if "bytes" in value:
            return base64.b64decode(value["bytes"])
        if "tuple" in value:
            return tuple(unpack(item) for item in value["tuple"])
        return {unpack(key): unpack(item) for key, item in value["map"]}
    return value


class DurableSaver(InMemorySaver):
    def __init__(self, factory, run_id, token):
        super().__init__()
        self.factory, self.run_id, self.token = factory, run_id, token
        with factory() as session:
            row = session.get(AgentRun, run_id)
            if row.checkpoint:
                data = unpack(row.checkpoint)
                for thread, namespaces in data["storage"].items():
                    for ns, checkpoints in namespaces.items():
                        self.storage[thread][ns].update(checkpoints)
                self.writes.update(data["writes"])
                self.blobs.update(data["blobs"])

    def flush(self, state=None):
        bundle = pack(
            {"storage": dict(self.storage), "writes": dict(self.writes), "blobs": self.blobs}
        )
        with self.factory() as session:
            row = session.get(AgentRun, self.run_id)
            if row.execution_token != self.token:
                raise RuntimeError("Execution lease changed")
            row.checkpoint = bundle
            row.updated_at = datetime.now(UTC)
            if state and "constraints" in state:
                row.state = {key: value for key, value in state.items() if not key.startswith("__")}
                row.status = state.get("status", "running")
            session.commit()

    def put(self, config, checkpoint, metadata, new_versions):
        result = super().put(config, checkpoint, metadata, new_versions)
        self.flush(checkpoint.get("channel_values"))
        return result

    def put_writes(self, config, writes, task_id, task_path=""):
        super().put_writes(config, writes, task_id, task_path)
        self.flush()
