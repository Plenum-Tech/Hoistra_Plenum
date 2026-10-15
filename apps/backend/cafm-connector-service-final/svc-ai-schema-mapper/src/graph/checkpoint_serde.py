"""The checkpointer's serializer: LangGraph's own, compressed when HOIST_CHECKPOINT_COMPRESS=1.

Every superstep of a migration writes its changed state to the checkpoint tables. The state is
msgpack, which zlib (level 1, ~10 ms for a whole state) shrinks about 7x — on a 2.8 Mbit/s uplink the
difference between minutes and seconds between gates (2 Oct 2026). Lossless: loads gives back what
LangGraph's serializer gives back. A value stored before (plain "msgpack", "bytes", "null", …) loads
unchanged, so a run paused before this shipped resumes after it.

Reading a compressed value always works; WRITING one is switched on by HOIST_CHECKPOINT_COMPRESS=1,
because a deployment older than this module cannot read it (the old revision during a deploy, a
rollback, and every reader of the shared checkpoint tables). Turn it on only once every deployment
that reads these tables runs this code.
"""
from __future__ import annotations

import os
import zlib
from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_SUFFIX = "+zlib"
#: Smaller values are stored as they are: compressing them saves nothing worth a decompress.
_MIN_BYTES = 1024


class CompressedJsonPlus(JsonPlusSerializer):
    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        type_, data = super().dumps_typed(obj)
        if data and len(data) >= _MIN_BYTES and os.environ.get("HOIST_CHECKPOINT_COMPRESS") == "1":
            return type_ + _SUFFIX, zlib.compress(data, 1)
        return type_, data

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_, payload = data
        if type_.endswith(_SUFFIX):
            return super().loads_typed((type_[: -len(_SUFFIX)], zlib.decompress(payload)))
        return super().loads_typed(data)
