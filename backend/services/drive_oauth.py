"""OAuth 2.0 Device Flow para Google Drive.

Permite vincular la cuenta de Drive desde el panel web sin instalar rclone
en el equipo del cliente. El usuario abre `verification_url` en cualquier
dispositivo (móvil, laptop), teclea el `user_code` mostrado en la UI,
autoriza, y el backend hace polling hasta recibir el token.

Docs: https://developers.google.com/identity/protocols/oauth2/limited-input-device
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


class OAuthError(Exception):
    """Error de configuración o comunicación con Google."""


class OAuthPending(Exception):
    """Usuario aún no ha completado la autorización (seguir polling)."""

    def __init__(self, code: str, slow_down: bool = False):
        super().__init__(code)
        self.code = code
        self.slow_down = slow_down


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int

    def to_public_dict(self) -> dict:
        """Lo que mandamos al frontend. El device_code también va (stateless)."""
        return {
            "device_code": self.device_code,
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "expires_in": self.expires_in,
            "interval": self.interval,
        }


def _post_form(url: str, data: dict, timeout: int = 15) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": "http_error", "error_description": raw}
        return e.code, payload


def start_device_flow(client_id: str, scope: str) -> DeviceCode:
    if not client_id:
        raise OAuthError(
            "GOOGLE_CLIENT_ID no está configurado. Edita "
            "/opt/snapshot-V3/core/etc/snapshot.conf y reinicia el backend."
        )
    status, payload = _post_form(DEVICE_CODE_URL, {"client_id": client_id, "scope": scope})
    if status != 200:
        err = payload.get("error_description") or payload.get("error") or f"HTTP {status}"
        raise OAuthError(f"Google rechazó la solicitud: {err}")
    try:
        return DeviceCode(
            device_code=payload["device_code"],
            user_code=payload["user_code"],
            verification_url=payload.get("verification_url") or payload.get("verification_uri"),
            expires_in=int(payload.get("expires_in", 1800)),
            interval=int(payload.get("interval", 5)),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise OAuthError(f"Respuesta inesperada de Google: {e}")


def poll_device_token(client_id: str, client_secret: str, device_code: str) -> dict:
    """Consulta a Google si el usuario ya autorizó.

    Éxito → dict con access_token/refresh_token/expires_in/token_type/scope.
    Si sigue pendiente → lanza OAuthPending.
    Errores terminales → OAuthError.
    """
    if not client_id or not client_secret:
        raise OAuthError("credenciales OAuth no configuradas")
    status, payload = _post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "device_code": device_code,
        "grant_type": DEVICE_GRANT,
    })
    if status == 200 and payload.get("access_token"):
        return payload
    err = payload.get("error", "")
    if err == "authorization_pending":
        raise OAuthPending(err, slow_down=False)
    if err == "slow_down":
        raise OAuthPending(err, slow_down=True)
    if err == "access_denied":
        raise OAuthError("El usuario rechazó la autorización.")
    if err == "expired_token":
        raise OAuthError("El código expiró. Genera uno nuevo.")
    msg = payload.get("error_description") or err or f"HTTP {status}"
    raise OAuthError(f"Google devolvió error: {msg}")


def build_rclone_token_json(token_payload: dict) -> str:
    """Convierte la respuesta de Google al formato de token que espera rclone."""
    expires_in = int(token_payload.get("expires_in", 3600))
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    # RFC3339 con 'Z'
    expiry_iso = expiry.strftime("%Y-%m-%dT%H:%M:%S.") + f"{expiry.microsecond * 1000:09d}Z"
    rclone_token = {
        "access_token": token_payload["access_token"],
        "token_type": token_payload.get("token_type", "Bearer"),
        "refresh_token": token_payload.get("refresh_token", ""),
        "expiry": expiry_iso,
    }
    if not rclone_token["refresh_token"]:
        raise OAuthError(
            "Google no devolvió refresh_token. "
            "Si ya habías autorizado esta app antes, revoca el acceso en "
            "https://myaccount.google.com/permissions y vuelve a intentar."
        )
    return json.dumps(rclone_token)
