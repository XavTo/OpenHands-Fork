from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from openhands.agent_server.utils import utc_now


class SandboxSpecInfo(BaseModel):
    """A template for creating a Sandbox (e.g: A Docker Image vs Container)."""

    id: str
    command: list[str] | None
    created_at: datetime = Field(default_factory=utc_now)
    initial_env: dict[str, str] = Field(
        default_factory=dict, description='Initial Environment Variables'
    )
    working_dir: str = '/home/openhands/workspace'

    @field_validator('working_dir', mode='before')
    @classmethod
    def normalize_working_dir(cls, value: str | None) -> str:
        """Ensure working_dir is never empty for subprocess cwd usage."""
        if value is None:
            return '.'
        if isinstance(value, str) and value.strip() == '':
            return '.'
        return value


class SandboxSpecInfoPage(BaseModel):
    items: list[SandboxSpecInfo]
    next_page_id: str | None = None
