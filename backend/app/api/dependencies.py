from typing import cast

from fastapi import Request

from app.core.config import Settings
from app.infrastructure.services import Services


def get_services(request: Request) -> Services:
    return cast(Services, request.app.state.services)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)
