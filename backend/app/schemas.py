from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """JSON uses camelCase (matches the React types); Python uses snake_case. Can be built from ORM objects."""
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class UserOut(CamelModel):
    id: str
    name: str
    role: str


class AppointmentOut(CamelModel):
    id: int
    patient_id: str
    patient_name: str
    provider_id: str
    provider_name: str
    start_time: datetime
    end_time: datetime
    appointment_type: str
    reason: str
    status: str
    version: int


class EventOut(CamelModel):
    id: int
    appointment_id: int
    actor_role: str
    actor_name: str
    action: str
    from_status: str | None
    to_status: str
    from_start: datetime | None
    to_start: datetime
    from_end: datetime | None
    to_end: datetime
    created_at: datetime


class AuditEntryOut(EventOut):
    """One row of the global audit log: the event plus enough appointment context to read it on its own."""
    appointment_type: str
    patient_name: str
    provider_name: str


class NotificationOut(CamelModel):
    id: int
    appointment_id: int
    kind: str
    channel: str
    subject: str
    body: str
    delivery_status: str
    gave_up: bool
    created_at: datetime
    read_at: datetime | None


class CreateIn(CamelModel):
    provider_id: str
    start_time: datetime
    duration_min: int = Field(30, ge=15, le=120)
    appointment_type: str = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=500)


class VersionIn(CamelModel):
    version: int


class RescheduleIn(VersionIn):
    start_time: datetime
