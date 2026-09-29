from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict


KST = ZoneInfo("Asia/Seoul")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ErrorResponse(StrictModel):
    message: str
    data: dict | None = None
