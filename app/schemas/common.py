from pydantic import BaseModel


class ErrorResponse(BaseModel):
    message: str
    data: dict | None = None
