"""Sign in with ChatGPT: OAuth with PKCE and a loopback callback, tokens under DPAPI (SPEC D18, ARCHITECTURE 6.9).

OpenAI's flow for open-source, locally hosted apps. The agent:
1. keeps a random `ext_agent_host_id` for this PC (auth\\host_id);
2. listens once on http://127.0.0.1:1455/auth/callback (another free port if 1455 is taken), never `localhost`;
3. hands the window an authorize URL (PKCE S256, fresh state and nonce, `client_id=dynamic_agent_client` the
   first time, the issued `oaiapp_...` id after that), which the host opens in the system browser;
4. on the callback checks `state`, exchanges the code (no client secret) and checks the ID token's issuer,
   audience, expiry and nonce, and that the grant includes `chatgpt.tokens.use.direct`;
5. closes the listener after one callback or 5 minutes.

The ID token's signature is not checked against JWKS: it arrives straight from the token endpoint over TLS
(OpenID Connect Core 3.1.3.7, item 6 allows TLS server validation in place of the signature then), and checking
it would need a JWT and crypto dependency for no added assurance. Claims are read only to validate the grant and
to show which account is signed in.

Tokens: auth\\chatgpt.bin, the JSON token set encrypted with DPAPI for the current user, written atomically in a
user-only folder (main.py restricts it at startup). Never in the database, backups, logs or the window; log lines
carry outcomes only. Refresh rotates both tokens; a refused refresh signs out.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from jobforge_agent import logs, winutil

AUTH = "https://auth.openai.com/api/accounts"
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-luna"  # preselected in the window (Kevin, 2026-10-25); the person picks from the plan's list
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
FIRST_CLIENT_ID = "dynamic_agent_client"
CALLBACK_PORT = 1455
CALLBACK_PATH = "/auth/callback"
SIGN_IN_TIMEOUT_S = 300
REFRESH_MARGIN_S = 120
HTTP_TIMEOUT_S = 30.0

_PAGE = ("<!doctype html><meta charset=utf-8><title>JobForge</title>"
         "<body style='font-family:Segoe UI,sans-serif;margin:3em'><h1>{title}</h1><p>{text}</p></body>")


class AuthError(Exception):
    """kind: signed_out (no tokens, or a refresh was refused) / unavailable (the sign-in service did not answer)."""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt_claims(token: str) -> dict:
    """The claims of a JWT, unverified (see the module note on signatures)."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def check_id_token(id_token: str, client_id: str, nonce: str, now: float) -> str | None:
    """Why the ID token is not acceptable, or None."""
    c = jwt_claims(id_token)
    aud = c.get("aud")
    if not c:
        return "unreadable ID token"
    if c.get("iss") != ISSUER:
        return "unexpected issuer"
    if client_id not in (aud if isinstance(aud, list) else [aud]):
        return "ID token is for another app"
    if not isinstance(c.get("exp"), (int, float)) or c["exp"] < now:
        return "ID token expired"
    if c.get("nonce") != nonce:
        return "nonce mismatch"
    return None


@dataclass
class SignIn:
    """One sign-in attempt: its state for the window, and the secrets the callback is checked against."""
    state: str = "idle"  # idle / waiting / done / failed
    error: str | None = None
    url: str | None = None
    redirect: str | None = None
    verifier: str = ""
    oauth_state: str = ""
    nonce: str = ""
    server: asyncio.AbstractServer | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


class ChatGPTAuth:
    def __init__(self, folder: Path, http: httpx.AsyncClient | None = None, now: Callable[[], float] = time.time,
                 on_change: Callable[[], None] = lambda: None, transport: httpx.AsyncBaseTransport | None = None):
        self.folder = folder
        self.file = folder / "chatgpt.bin"
        # trust_env=False: no HTTP_PROXY or system proxy ever sees a code, a token or a prompt.
        # `transport`: tests stand in for auth.openai.com through the agent's inference transport.
        self.http = http or httpx.AsyncClient(transport=transport, trust_env=False, timeout=HTTP_TIMEOUT_S)
        self.now = now
        self.on_change = on_change  # the window's model card refreshes on a sign-in or sign-out
        self.log = logs.get("chatgpt")
        self.attempt = SignIn()
        self._lock = asyncio.Lock()
        self._timeout_task: asyncio.Task | None = None

    async def close(self) -> None:
        await self._close_listener()
        await self.http.aclose()

    # ----- stored tokens -----

    def _load(self) -> dict | None:
        try:
            return json.loads(winutil.dpapi_unprotect(self.file.read_bytes()))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            # Unreadable (another user's or a damaged file): visible in the log, treated as signed out.
            self.log.error("chatgpt_tokens_unreadable", error=type(exc).__name__)
            return None

    def _save(self, t: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_bytes(winutil.dpapi_protect(json.dumps(t).encode()))
        os.replace(tmp, self.file)

    def host_id(self) -> str:
        p = self.folder / "host_id"
        try:
            return p.read_text().strip()
        except FileNotFoundError:
            self.folder.mkdir(parents=True, exist_ok=True)
            hid = f"urn:uuid:{uuid.uuid4()}"
            p.write_text(hid)
            return hid

    def signed_in(self) -> bool:
        return self.file.exists()

    def status(self) -> dict:
        t = self._load()
        account = jwt_claims(t.get("id_token") or "") if t else {}
        return {"signed_in": t is not None, "email": account.get("email"),
                "sign_in": {"state": self.attempt.state, "error": self.attempt.error}}

    def sign_out(self, reason: str = "user") -> bool:
        try:
            self.file.unlink()
        except FileNotFoundError:
            return False
        self.log.info("chatgpt_signed_out", reason=reason)
        self.on_change()
        return True

    async def access_token(self) -> str:
        """A current access token, refreshed when it is about to expire. Raises AuthError."""
        async with self._lock:
            t = self._load()
            if t is None:
                raise AuthError("signed_out", "not signed in to ChatGPT")
            now = self.now()
            due = t["expires_at"] - now < REFRESH_MARGIN_S
            # earliest_refresh_at (in the token answer, not in the docs): no refresh before it unless expired.
            early = now < float(t.get("earliest_refresh_at") or 0) and t["expires_at"] > now
            if not due or early:
                return t["access_token"]
            try:
                r = await self.http.post(f"{AUTH}/oauth/token", data={
                    "grant_type": "refresh_token", "client_id": t["client_id"], "refresh_token": t["refresh_token"],
                    "resource": RESOURCE})
            except httpx.TransportError as exc:
                raise AuthError("unavailable", f"token refresh: {type(exc).__name__}") from None
            if r.status_code in (400, 401):
                self.log.warning("chatgpt_refresh_refused", status=r.status_code)
                self.sign_out("refresh_refused")
                raise AuthError("signed_out", "ChatGPT sign-in expired; sign in again")
            if r.status_code != 200:
                raise AuthError("unavailable", f"token refresh: HTTP {r.status_code}")
            self._save(self._token_set(r.json(), t["client_id"], fallback=t))
            self.log.info("chatgpt_token_refreshed")
            return self._load()["access_token"]

    def _token_set(self, j: dict, client_id: str, fallback: dict | None = None) -> dict:
        fb = fallback or {}
        now = self.now()
        out = {"client_id": client_id, "access_token": j["access_token"],
               "refresh_token": j.get("refresh_token") or fb.get("refresh_token"),
               "id_token": j.get("id_token") or fb.get("id_token"), "scope": j.get("scope") or fb.get("scope", ""),
               "expires_at": now + float(j.get("expires_in", 3600))}
        era = j.get("earliest_refresh_at")
        if isinstance(era, (int, float)):
            out["earliest_refresh_at"] = float(era)
        return out

    # ----- sign-in -----

    async def start_sign_in(self) -> str:
        """Start listening for the callback and return the authorize URL for the system browser."""
        await self._close_listener()
        a = SignIn(state="waiting")
        a.verifier = _b64url(secrets.token_bytes(48))
        a.oauth_state, a.nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        try:
            a.server = await asyncio.start_server(self._on_connection, "127.0.0.1", CALLBACK_PORT)
        except OSError:
            a.server = await asyncio.start_server(self._on_connection, "127.0.0.1", 0)
            self.log.warning("chatgpt_callback_port_busy", port=CALLBACK_PORT)
        port = a.server.sockets[0].getsockname()[1]
        a.redirect = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
        t = self._load()
        params = {"client_id": (t or {}).get("client_id") or FIRST_CLIENT_ID, "agent_name_hint": "JobForge",
                  "ext_agent_host_id": self.host_id(), "response_type": "code", "redirect_uri": a.redirect,
                  "scope": SCOPES, "resource": RESOURCE, "state": a.oauth_state, "nonce": a.nonce,
                  "code_challenge_method": "S256",
                  "code_challenge": _b64url(hashlib.sha256(a.verifier.encode()).digest())}
        a.url = f"{AUTH}/authorize?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        self.attempt = a
        self._timeout_task = asyncio.create_task(self._expire(a))
        self.log.info("chatgpt_sign_in_started", port=port)
        return a.url

    async def _expire(self, a: SignIn) -> None:
        try:
            await asyncio.wait_for(a.done.wait(), SIGN_IN_TIMEOUT_S)
        except asyncio.TimeoutError:
            if self.attempt is a and a.state == "waiting":
                self._finish(a, "failed", "no answer from the browser within 5 minutes")
        await self._close(a)

    async def _close(self, a: SignIn) -> None:
        if a.server is not None:
            a.server.close()
            await a.server.wait_closed()
            a.server = None

    async def _close_listener(self) -> None:
        if self._timeout_task is not None:
            self._timeout_task.cancel()
            self._timeout_task = None
        await self._close(self.attempt)

    def _finish(self, a: SignIn, state: str, error: str | None) -> None:
        a.state, a.error = state, error
        a.done.set()
        if state == "done":
            self.log.info("chatgpt_signed_in")
        else:
            self.log.warning("chatgpt_sign_in_failed", error=error)
        self.on_change()

    async def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        a = self.attempt
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                pass  # headers: nothing needed from them
            parts = line.split(" ")
            target = urllib.parse.urlparse(parts[1] if len(parts) > 1 else "/")
            if parts[0] != "GET" or target.path != CALLBACK_PATH or a.state != "waiting":
                await self._respond(writer, 404, "Not found", "")
                return
            q = {k: v[0] for k, v in urllib.parse.parse_qs(target.query).items()}
            error = await self._complete(a, q)
            self._finish(a, "failed" if error else "done", error)
            if error:
                await self._respond(writer, 400, "Sign-in did not finish", f"{error}. Go back to JobForge and try again.")
            else:
                await self._respond(writer, 200, "Signed in to ChatGPT", "You can close this tab and go back to JobForge.")
        except (asyncio.TimeoutError, ConnectionError, UnicodeError):
            return
        finally:
            writer.close()

    async def _respond(self, writer: asyncio.StreamWriter, status: int, title: str, text: str) -> None:
        body = _PAGE.format(title=title, text=text).encode() if title else b""
        writer.write(f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\nContent-Type: text/html; charset=utf-8\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _complete(self, a: SignIn, q: dict) -> str | None:
        """Check the callback and exchange the code. Returns why it failed, or None."""
        if q.get("state") != a.oauth_state:
            return "the answer did not match this sign-in (state)"
        if "error" in q:
            return f"ChatGPT refused the sign-in ({q['error'][:80]})"
        client_id, code = q.get("client_id"), q.get("code")
        if not client_id or not code:
            return "the answer had no code"
        try:
            r = await self.http.post(f"{AUTH}/oauth/token", data={
                "grant_type": "authorization_code", "client_id": client_id, "code": code, "code_verifier": a.verifier,
                "redirect_uri": a.redirect, "resource": RESOURCE})
        except httpx.TransportError as exc:
            return f"the sign-in service did not answer ({type(exc).__name__})"
        if r.status_code != 200:
            return f"the code exchange failed (HTTP {r.status_code})"
        j = r.json()
        if PLAN_SCOPE not in (j.get("scope") or "").split():
            return "ChatGPT did not allow using your plan from JobForge"
        why = check_id_token(j.get("id_token") or "", client_id, a.nonce, self.now())
        if why:
            return why
        self._save(self._token_set(j, client_id))
        return None
