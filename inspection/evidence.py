"""'Saved, hashed and timestamped': tamper-evident records.

Every observation gets a SHA-256 record hash over its content (incl. the SHA-256 of the original
photo) and the previous record's hash of the same unit, forming a hash chain. Changing or deleting
any stored record breaks the chain from that point on (`verify_chain`).
"""

import hashlib
import json

GENESIS = "0" * 64


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_hash(fields: dict, prev_hash: str) -> str:
    payload = json.dumps({**fields, "prev_hash": prev_hash}, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode())


def verify_chain(records: list[tuple[dict, str, str]]) -> dict:
    """records: [(fields, prev_hash, record_hash)] in chain order."""
    prev = GENESIS
    for i, (fields, p, h) in enumerate(records):
        if p != prev or record_hash(fields, p) != h:
            return {"ok": False, "n_records": len(records), "broken_at": i}
        prev = h
    return {"ok": True, "n_records": len(records), "head": prev}
