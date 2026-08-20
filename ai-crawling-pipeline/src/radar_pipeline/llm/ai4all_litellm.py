import datetime
import json
import os
from collections.abc import AsyncGenerator
from typing import Any

import boto3
import jwt
import litellm
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SERVICE_ACC_SECRET_NAME = "gcp-service-account-credentials"
PROXY_SECRET_NAME = "litellm-api-key"
LITELLM_PROXY_API_BASE = "https://cp2677.apps-test.valeo.com/engine/litellm"
AUDIANCE = "34928367053-d20hpau255ihrvkvs29vn4rd1ig9shei.apps.googleusercontent.com"


_token: str | None = None
_token_expiry: datetime.datetime | None = None
_credentials: service_account.IDTokenCredentials | None = None
_current_audience: str | None = None


def _get_secret(secret_name: str, region_name: str = "eu-west-1"):
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def _get_or_refresh_token() -> str:
    global _token, _token_expiry, _credentials, _current_audience
    now = datetime.datetime.now(datetime.UTC)
    buffer = datetime.timedelta(minutes=5)
    audience = AUDIANCE

    try:
        if _token and _token_expiry and now < (_token_expiry - buffer):
            return _token
    except (TypeError, AttributeError):
        pass

    service_account_info = _get_secret(SERVICE_ACC_SECRET_NAME)

    try:
        if _credentials is None or _current_audience != audience:
            _credentials = service_account.IDTokenCredentials.from_service_account_info(
                service_account_info, target_audience=audience
            )
            _current_audience = audience

        request = Request()
        _credentials.refresh(request)
        _token = _credentials.token

        decoded = jwt.decode(_token, options={"verify_signature": False})
        exp_timestamp: int | None = decoded.get("exp")
        if exp_timestamp:
            _token_expiry = datetime.datetime.fromtimestamp(
                exp_timestamp, tz=datetime.UTC
            )
        return _token
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Error refreshing token: {e}")


class AI4ALLLiteLlm:
    def __init__(
        self,
        model: str = "litellm_proxy/claude-sonnet-4-5",
        api_key: str | None = None,
        api_base: str | None = None,
        **kwargs,
    ):
        self.model = model
        self.api_base = api_base or LITELLM_PROXY_API_BASE
        self.kwargs = kwargs

        if api_key is None:
            api_key = _get_secret(PROXY_SECRET_NAME)["LiteLLM Key"]
        self.api_key = api_key

    def _get_extra_headers(self) -> dict:
        global _token
        if _token is None:
            _token = _get_or_refresh_token()

        user_id = (
            os.getenv("AI4ALL_USER_ID")
            or os.getenv("USER")
            or os.getenv("USERNAME")
            or "local-user"
        )

        return {
            "Proxy-Authorization": f"Bearer {_token}",
            "x-ai4all-user-email": user_id,
        }

    def complete(self, messages: list[dict], **kwargs) -> Any:
        extra_headers = self._get_extra_headers()
        return litellm.completion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            api_base=self.api_base,
            extra_headers=extra_headers,
            **{**self.kwargs, **kwargs},
        )

    async def acomplete(self, messages: list[dict], **kwargs) -> Any:
        extra_headers = self._get_extra_headers()
        return await litellm.acompletion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            api_base=self.api_base,
            extra_headers=extra_headers,
            **{**self.kwargs, **kwargs},
        )

    def stream_complete(self, messages: list[dict], **kwargs) -> Any:
        extra_headers = self._get_extra_headers()
        return litellm.completion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            api_base=self.api_base,
            extra_headers=extra_headers,
            stream=True,
            **{**self.kwargs, **kwargs},
        )

    async def astream_complete(self, messages: list[dict], **kwargs) -> AsyncGenerator[Any]:
        extra_headers = self._get_extra_headers()
        stream = await litellm.acompletion(
            model=self.model,
            messages=messages,
            api_key=self.api_key,
            api_base=self.api_base,
            extra_headers=extra_headers,
            stream=True,
            **{**self.kwargs, **kwargs},
        )
        async for chunk in stream:
            yield chunk

    def __repr__(self) -> str:
        return f"AI4ALLLiteLlm(model={self.model})"


def get_ai4all_model(
    model_name: str = "litellm_proxy/claude-sonnet-4-5",
    api_key: str | None = None,
    api_base: str | None = None,
) -> AI4ALLLiteLlm:
    return AI4ALLLiteLlm(
        model=model_name,
        api_key=api_key,
        api_base=api_base,
    )
