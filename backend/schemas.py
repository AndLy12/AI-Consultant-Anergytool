from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewChat(StrictModel):
    title: str = Field(default="Новый диалог", min_length=1, max_length=120)


class SendMessage(StrictModel):
    content: str = Field(min_length=1, max_length=8000)
    request_id: str

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value

    @field_validator("request_id")
    @classmethod
    def uuid_key(cls, value: str) -> str:
        return str(UUID(value))


class Source(StrictModel):
    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=8000)
    section: str | None = Field(default=None, max_length=500)
    page: int | None = Field(default=None, ge=1)


class Answer(StrictModel):
    answer: str = Field(min_length=1, max_length=10000)
    status: Literal["stub", "answered", "clarification_needed", "insufficient_data", "out_of_scope"]
    sources: list[Source] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def grounded_answer(self):
        if not self.answer.strip():
            raise ValueError("Empty answer")
        if self.status == "answered" and not self.sources:
            raise ValueError("Answered status requires sources")
        return self
