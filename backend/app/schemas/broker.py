from typing import Optional
from pydantic import BaseModel, Field


class BrokerConnectRequest(BaseModel):
    broker_name: str = Field(..., description="Broker slug, e.g. 'dhan'")
    api_key: str = Field(..., min_length=4, max_length=128)
    api_secret: Optional[str] = Field("", max_length=256)
    access_token: Optional[str] = Field(None, max_length=4096)
    account_id: Optional[str] = Field(None, max_length=80)
    label: Optional[str] = Field(None, max_length=80)
    is_paper: bool = False
