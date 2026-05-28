import json
from .connection import db


class AuditLogger:
    async def log(
        self,
        key_id: str | None,
        action: str,
        domain: str | None = None,
        request_id: str | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        metadata: dict | None = None,
    ):
        conn = await db.pool.acquire()
        try:
            await conn.execute(
                """INSERT INTO audit_log (key_id, action, domain, request_id, status, duration_ms, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                key_id, action, domain, request_id, status, duration_ms,
                json.dumps(metadata) if metadata else None
            )
        finally:
            await db.pool.release(conn)
