"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr


class SubscribeRequest(BaseModel):
    email: EmailStr


class SubscribeResponse(BaseModel):
    ok: bool = True
    message: str = "Confirmation email sent. Please check your inbox."


class StatusResponse(BaseModel):
    ok: bool = True
    message: str = ""


class PostMeta(BaseModel):
    slug: str
    title: str
    date: Optional[datetime] = None
    summary: Optional[str] = None
    author: Optional[str] = None


class PostListResponse(BaseModel):
    posts: List[PostMeta]


class DeliveryStats(BaseModel):
    post_slug: str
    sent: int
    failed: int
