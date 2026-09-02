from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from jwt import InvalidTokenError, PyJWK

from ..config import SecuritySettings


OIDC_FLOW_TTL_SECONDS = 600
OIDC_ALLOWED_SIGNING_ALGORITHMS = frozenset({"RS256", "PS256", "ES256"})


class OidcProtocolError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OidcDiscovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    signing_algorithms: tuple[str, ...]


@dataclass(frozen=True)
class OidcFlow:
    state: str
    nonce: str
    code_verifier: str
    expires_at: int


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _is_allowed_provider_url(url: str, settings: SecuritySettings) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return bool(
        settings.oidc_allow_insecure_localhost
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
    )


def create_flow() -> OidcFlow:
    return OidcFlow(
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64),
        expires_at=int(time.time()) + OIDC_FLOW_TTL_SECONDS,
    )


def encode_flow_cookie(flow: OidcFlow, secret_key: str) -> str:
    payload = _b64url(
        json.dumps(
            {
                "state": flow.state,
                "nonce": flow.nonce,
                "code_verifier": flow.code_verifier,
                "exp": flow.expires_at,
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _b64url(hmac.new(secret_key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def decode_flow_cookie(value: str, secret_key: str) -> OidcFlow:
    try:
        payload, supplied_signature = value.split(".", 1)
        expected_signature = _b64url(
            hmac.new(secret_key.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("signature")
        data = json.loads(_b64url_decode(payload))
        flow = OidcFlow(
            state=str(data["state"]),
            nonce=str(data["nonce"]),
            code_verifier=str(data["code_verifier"]),
            expires_at=int(data["exp"]),
        )
        if flow.expires_at <= int(time.time()):
            raise OidcProtocolError("OIDC_FLOW_EXPIRED", "OIDC 로그인 요청이 만료되었습니다.")
        if not flow.state or not flow.nonce or not 43 <= len(flow.code_verifier) <= 128:
            raise ValueError("flow")
        return flow
    except OidcProtocolError:
        raise
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise OidcProtocolError("OIDC_FLOW_INVALID", "OIDC 로그인 요청을 확인할 수 없습니다.") from exc


async def fetch_discovery(settings: SecuritySettings) -> OidcDiscovery:
    issuer = str(settings.oidc_issuer_url).rstrip("/")
    url = f"{issuer}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=settings.oidc_timeout_seconds, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcProtocolError("OIDC_PROVIDER_UNAVAILABLE", "사내 인증 서버에 연결할 수 없습니다.", 503) from exc

    if data.get("issuer") != issuer:
        raise OidcProtocolError("OIDC_ISSUER_MISMATCH", "OIDC issuer가 설정과 일치하지 않습니다.", 503)
    endpoints = [data.get("authorization_endpoint"), data.get("token_endpoint"), data.get("jwks_uri")]
    if any(not isinstance(value, str) or not _is_allowed_provider_url(value, settings) for value in endpoints):
        raise OidcProtocolError("OIDC_DISCOVERY_INVALID", "OIDC discovery endpoint가 안전하지 않습니다.", 503)
    advertised = data.get("id_token_signing_alg_values_supported") or ["RS256"]
    algorithms = tuple(value for value in advertised if value in OIDC_ALLOWED_SIGNING_ALGORITHMS)
    if not algorithms:
        raise OidcProtocolError("OIDC_SIGNING_ALGORITHM_UNSUPPORTED", "지원되는 OIDC 서명 알고리즘이 없습니다.", 503)
    return OidcDiscovery(issuer, endpoints[0], endpoints[1], endpoints[2], algorithms)


def authorization_url(settings: SecuritySettings, discovery: OidcDiscovery, flow: OidcFlow) -> str:
    challenge = _b64url(hashlib.sha256(flow.code_verifier.encode("ascii")).digest())
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": settings.oidc_scopes,
            "state": flow.state,
            "nonce": flow.nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in discovery.authorization_endpoint else "?"
    return f"{discovery.authorization_endpoint}{separator}{query}"


async def exchange_code(
    settings: SecuritySettings,
    discovery: OidcDiscovery,
    *,
    code: str,
    code_verifier: str,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=settings.oidc_timeout_seconds, follow_redirects=False) as client:
            response = await client.post(
                discovery.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "client_id": settings.oidc_client_id,
                    "client_secret": settings.oidc_client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OidcProtocolError("OIDC_CODE_EXCHANGE_FAILED", "OIDC 인증 코드를 교환하지 못했습니다.") from exc
    id_token = data.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise OidcProtocolError("OIDC_ID_TOKEN_MISSING", "OIDC ID token이 없습니다.")
    return id_token


async def validate_id_token(
    settings: SecuritySettings,
    discovery: OidcDiscovery,
    *,
    id_token: str,
    expected_nonce: str,
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm not in OIDC_ALLOWED_SIGNING_ALGORITHMS or algorithm not in discovery.signing_algorithms:
            raise OidcProtocolError("OIDC_SIGNING_ALGORITHM_INVALID", "OIDC ID token 서명 알고리즘이 허용되지 않습니다.")
        if not isinstance(key_id, str) or not key_id:
            raise OidcProtocolError("OIDC_KEY_ID_MISSING", "OIDC ID token key id가 없습니다.")
        async with httpx.AsyncClient(timeout=settings.oidc_timeout_seconds, follow_redirects=False) as client:
            response = await client.get(discovery.jwks_uri, headers={"Accept": "application/json"})
            response.raise_for_status()
            jwks = response.json()
        candidates = [key for key in jwks.get("keys", []) if key.get("kid") == key_id]
        if len(candidates) != 1:
            raise OidcProtocolError("OIDC_SIGNING_KEY_NOT_FOUND", "OIDC 서명 키를 찾을 수 없습니다.")
        signing_key = PyJWK.from_dict(candidates[0], algorithm=algorithm).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=settings.oidc_client_id,
            issuer=discovery.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
    except OidcProtocolError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidTokenError) as exc:
        raise OidcProtocolError("OIDC_ID_TOKEN_INVALID", "OIDC ID token 검증에 실패했습니다.") from exc

    if not hmac.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise OidcProtocolError("OIDC_NONCE_MISMATCH", "OIDC nonce가 일치하지 않습니다.")
    audiences = claims.get("aud")
    if isinstance(audiences, list) and len(audiences) > 1 and claims.get("azp") != settings.oidc_client_id:
        raise OidcProtocolError("OIDC_AUTHORIZED_PARTY_INVALID", "OIDC authorized party가 일치하지 않습니다.")
    return dict(claims)
