"""
PKU IAAA SSO authentication.

Actual flow (reverse-engineered from OAuthLogin.js on iaaa.pku.edu.cn):
  1. GET /iaaa/getPublicKey.do → RSA public key (PEM)
  2. Encrypt password with RSA PKCS1v15 using that key
  3. POST /iaaa/oauthlogin.do with appid, userName, encrypted password, redirectUrl
     → returns JSON { success: true, token: "..." }
  4. GET campusLogin?token=... on course.pku.edu.cn → Blackboard session cookies

PKU CA is not in the default macOS trust store, so verify=False is used.
"""
from __future__ import annotations

import base64

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from config import settings

IAAA_BASE = "https://iaaa.pku.edu.cn"
# redirUrl sent to IAAA must match the registered value (http://), otherwise
# IAAA rejects the login with E12. The actual GET uses https:// because port 80
# on course.pku.edu.cn does not respond on some networks.
IAAA_REDIR_URL = "http://course.pku.edu.cn/webapps/bb-sso-BBLEARN/execute/authValidate/campusLogin"
BB_CAMPUS_LOGIN_URL = "https://course.pku.edu.cn/webapps/bb-sso-BBLEARN/execute/authValidate/campusLogin"
BB_BASE = "https://course.pku.edu.cn"


def _encrypt_password(password: str, public_key_pem: str) -> str:
    """RSA-encrypt password using PKCS1v15 (matching JSEncrypt behaviour)."""
    key = load_pem_public_key(public_key_pem.encode())
    assert isinstance(key, RSAPublicKey)
    encrypted = key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode()


def _login(client: httpx.Client, encrypted_pwd: str, otp_code: str) -> dict:
    """POST credentials to oauthlogin.do and return the JSON payload."""
    login_resp = client.post(
        f"{IAAA_BASE}/iaaa/oauthlogin.do",
        data={
            "appid": "blackboard",
            "userName": settings.pku_username,
            "password": encrypted_pwd,
            "randCode": "",
            "smsCode": "",
            "otpCode": otp_code,
            "redirUrl": IAAA_REDIR_URL,
        },
    )
    login_resp.raise_for_status()
    return login_resp.json()


def _session_from_cookie() -> "httpx.Client | None":
    """Build a session directly from a browser Cookie header (env BB_COOKIE).

    Use this when the IAAA account requires OTP or is rate-limited: log into
    course.pku.edu.cn in a browser, copy the `Cookie:` request header from
    DevTools → Network → any course.pku.edu.cn request, and put it in .env:
        BB_COOKIE=JSESSIONID=...; session_id=...; ...
    """
    import os
    from pathlib import Path
    cookie_file = settings.bb_cookie_file or ".bb_cookie"
    cookie = (
        os.environ.get("BB_COOKIE", "").strip()
        or settings.bb_cookie.strip()
        or (Path(cookie_file).read_text(encoding="utf-8")
            if Path(cookie_file).exists() else "")
    )
    # A copied header may span several lines; flatten to one.
    cookie = " ".join(part.strip() for part in cookie.splitlines() if part.strip())
    if not cookie:
        return None
    client = httpx.Client(
        base_url=BB_BASE, follow_redirects=True, timeout=30, verify=False,
        headers={
            "Cookie": cookie,
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        },
    )
    # Sanity check: an invalid/expired cookie gets bounced to the IAAA login
    # page; a valid one returns the homework list HTML.
    from crawler.pku_homework import HW_BASE
    resp = client.get(f"{HW_BASE}/getHomeWorkList.do",
                      params={"course_id": settings.course_id or "_0_1"})
    if resp.status_code != 200 or "iaaa" in str(resp.url.host):
        raise RuntimeError(
            "BB_COOKIE session is invalid or expired — copy a fresh Cookie "
            "header from a browser logged into course.pku.edu.cn."
        )
    return client


def _env_otp() -> str:
    import os
    return os.environ.get("PKU_OTP", "").strip()


def _prompt_otp() -> str:
    try:
        return input("IAAA OTP 动态口令 (6 digits): ").strip()
    except (EOFError, OSError):
        raise RuntimeError(
            "IAAA account requires an OTP code and stdin is not interactive. "
            "Run again with PKU_OTP=<6-digit code> in the environment."
        )


def get_session() -> httpx.Client:
    """Authenticate and return an httpx.Client with active Blackboard session.

    Auth order: BB_COOKIE (browser session, skips IAAA entirely) → IAAA
    password (+ OTP via PKU_OTP env var or interactive prompt).
    """
    if client := _session_from_cookie():
        return client

    if not settings.pku_username or not settings.pku_password:
        raise RuntimeError(
            "PKU_USERNAME and PKU_PASSWORD must be set in .env to authenticate."
        )

    client = httpx.Client(base_url=BB_BASE, follow_redirects=True, timeout=30, verify=False)

    # Step 1: fetch RSA public key
    key_resp = client.get(f"{IAAA_BASE}/iaaa/getPublicKey.do")
    key_resp.raise_for_status()
    key_data = key_resp.json()
    if not key_data.get("success"):
        raise RuntimeError(f"Failed to fetch IAAA public key: {key_data}")
    public_key_pem: str = key_data["key"]

    # Step 2: encrypt password
    encrypted_pwd = _encrypt_password(settings.pku_password, public_key_pem)

    # Step 3: POST credentials. If PKU_OTP is set, send it on the first attempt
    # (each POST counts toward IAAA's rate limit, so don't waste one on an
    # empty otpCode when we already have a code).
    payload = _login(client, encrypted_pwd, _env_otp())
    if not payload.get("success"):
        errors = payload.get("errors") or {}
        code = errors.get("code", "") if isinstance(errors, dict) else ""
        msg = errors.get("msg", "") if isinstance(errors, dict) else str(errors)
        if code == "E05" or "OTP" in str(msg):
            # env OTP already sent (or absent) — prompt for a fresh code
            payload = _login(client, encrypted_pwd, _prompt_otp())
    if not payload.get("success"):
        raise RuntimeError(f"IAAA login failed: {payload.get('errors') or payload}")

    token: str = payload["token"]

    # Step 4: exchange token for Blackboard session cookies
    bb_resp = client.get(BB_CAMPUS_LOGIN_URL, params={"token": token})
    bb_resp.raise_for_status()

    return client
