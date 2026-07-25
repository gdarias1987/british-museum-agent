from pydantic import BaseModel, ConfigDict, Field


class GalleryStatus(BaseModel):
    id: str
    name: str
    floor: str
    department: str
    status: str
    accessibility_notes: str


class IncidentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gallery_id: str = Field(min_length=2, max_length=128)
    category: str = Field(min_length=2, max_length=128)
    description: str = Field(min_length=5, max_length=2_000)
    priority: str = Field(pattern="^(low|medium|high)$")


class IncidentResponse(BaseModel):
    id: int
    gallery_id: str
    category: str
    description: str
    priority: str
    reported_by: str
    status: str
    created_at: str
