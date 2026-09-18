"""Local HTTP API for the Wallapop tracker."""

from .app import app, create_app

__all__ = ["app", "create_app"]
