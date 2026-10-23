from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.llm import AuditEntry, AuditSink
from backend.models import AuditLog


def db_audit_sink(engine: Engine) -> AuditSink:
    def write(entry: AuditEntry) -> None:
        with Session(engine) as session:
            session.add(AuditLog(kind="llm", **entry.__dict__))
            session.commit()

    return write
