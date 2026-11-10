"""Ownership-scoped data access. Every read/write goes through user_id so a user can never touch another user's rows."""
from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound
from app.services.common import as_uuid

M = TypeVar("M")


class OwnedRepository(Generic[M]):
    model: type[M]
    label = "Record"

    def __init__(self, db: Session, user_id):
        self.db = db
        self.user_id = as_uuid(user_id)

    def query(self):
        return select(self.model).where(self.model.user_id == self.user_id)  # type: ignore[attr-defined]

    def get(self, id_) -> M:
        obj = self.db.scalars(self.query().where(self.model.id == as_uuid(id_))).first()  # type: ignore[attr-defined]
        if obj is None:
            raise NotFound(f"{self.label} not found")
        return obj

    def find(self, id_) -> M | None:
        if id_ is None:
            return None
        return self.db.scalars(self.query().where(self.model.id == as_uuid(id_))).first()  # type: ignore[attr-defined]

    def add(self, obj: M) -> M:
        obj.user_id = self.user_id  # type: ignore[attr-defined]
        self.db.add(obj)
        self.db.flush()
        return obj
