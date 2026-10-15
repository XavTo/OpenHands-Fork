"""Credentials for requests back to this app's own protected endpoints."""

import base64
import os


def internal_auth_headers() -> dict[str, str]:
    user = os.getenv('OPENHANDS_BASIC_AUTH_USER')
    password = os.getenv('OPENHANDS_BASIC_AUTH_PASSWORD')
    if not user or not password:
        return {}
    credentials = base64.b64encode(f'{user}:{password}'.encode()).decode()
    return {'Authorization': f'Basic {credentials}'}
