from datetime import date, datetime

from sqlmodel import Field, SQLModel


class MedicalLeave(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    leave_date: date
    reason: str | None = None
    created_at: datetime | None = None
