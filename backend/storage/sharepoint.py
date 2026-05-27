"""
SharePointBackend — Fase S1

Usa Playwright con Edge (perfil existente del banco) para autenticarse
en SharePoint via SSO y luego llama a la REST API con el contexto del browser.

Requiere (una sola vez en la máquina del banco):
    uv run playwright install msedge
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote, urlparse

from playwright.async_api import BrowserContext, async_playwright

from .base import StorageBackend

log = logging.getLogger("sige.sharepoint")

# Tiempo de vida del X-RequestDigest en segundos (SharePoint lo emite por 30 min;
# renovamos a los 25 para tener margen)
_DIGEST_TTL = 25 * 60


def _enc(*parts: str) -> str:
    """Codifica partes de ruta para URLs de SharePoint (conserva /)."""
    return quote("/".join(parts), safe="/")


class SharePointBackend(StorageBackend):
    def __init__(self, config: dict) -> None:
        self._site_url: str = config["site_url"].rstrip("/")
        self._edge_profile: str = config.get("edge_profile", "Default")

        # Ruta relativa al servidor: /sites/SERVICIOSGENERALES663/Documentos compartidos/...
        parsed = urlparse(self._site_url)
        raw_base = config["base_path"].strip("/")
        self._server_rel: str = parsed.path.rstrip("/") + "/" + _enc(raw_base)

        # Subcarpetas (rutas codificadas completas)
        self._pending_rel   = self._server_rel + "/events_pending"
        self._processed_rel = self._server_rel + "/events_processed"
        self._error_rel     = self._server_rel + "/events_error"
        self._master_rel    = self._server_rel + "/master"

        self._context: BrowserContext | None = None
        self._playwright = None

        self._digest: str | None = None
        self._digest_ts: float = 0.0

        self._folders_ensured = False

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def _ensure_context(self) -> BrowserContext:
        if self._context is not None:
            return self._context

        # Perfil aislado dentro del proyecto — evita conflicto con Edge del usuario
        project_root = Path(__file__).resolve().parent.parent.parent
        user_data_dir = str(project_root / "data" / "edge_profile")

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="msedge",
            headless=True,
            args=[
                f"--profile-directory={self._edge_profile}",
                "--disable-extensions",
                "--no-first-run",
                "--disable-background-networking",
            ],
        )

        # Warm-up: abre el site para que SSO/cookies de sesión estén activos
        page = await self._context.new_page()
        try:
            await page.goto(self._site_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as exc:
            log.warning("SharePoint warm-up: %s", exc)
        finally:
            await page.close()

        log.info("SharePoint context listo — site: %s", self._site_url)
        return self._context

    async def warmup(self) -> None:
        try:
            await self._ensure_context()
            log.info("SharePoint warmup completado")
        except Exception as exc:
            log.warning("SharePoint warmup falló (se reintentará en la primera llamada): %s", exc)

    async def close(self) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    # ── X-RequestDigest ───────────────────────────────────────────────────────

    async def _get_digest(self) -> str:
        now = time.monotonic()
        if self._digest and (now - self._digest_ts) < _DIGEST_TTL:
            return self._digest

        ctx = await self._ensure_context()
        resp = await ctx.request.fetch(
            f"{self._site_url}/_api/contextinfo",
            method="POST",
            headers={"Accept": "application/json;odata=verbose"},
        )
        if not resp.ok:
            raise RuntimeError(f"contextinfo falló: {resp.status}")
        data = await resp.json()
        self._digest = data["d"]["GetContextWebInformation"]["FormDigestValue"]
        self._digest_ts = now
        return self._digest

    # ── Helpers REST ──────────────────────────────────────────────────────────

    async def _api(self, method: str, url: str, **kwargs) -> dict | bytes | None:
        """Llama a la SharePoint REST API. Devuelve dict si JSON, bytes si binario."""
        ctx = await self._ensure_context()
        headers = kwargs.pop("headers", {})
        headers.setdefault("Accept", "application/json;odata=verbose")
        if method in ("POST", "PUT", "MERGE", "DELETE"):
            headers["X-RequestDigest"] = await self._get_digest()
        resp = await ctx.request.fetch(url, method=method, headers=headers, **kwargs)
        if resp.status == 404:
            return None
        if not resp.ok:
            body = await resp.text()
            raise RuntimeError(f"SharePoint {method} {url} → {resp.status}: {body[:200]}")
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type:
            return await resp.json()
        return await resp.body()

    async def _ensure_subfolders(self) -> None:
        if self._folders_ensured:
            return
        for rel in (
            self._pending_rel,
            self._processed_rel,
            self._error_rel,
            self._master_rel,
        ):
            folder_name = rel.rsplit("/", 1)[-1]
            parent_rel  = rel.rsplit("/", 1)[0]
            url = (
                f"{self._site_url}/_api/web"
                f"/GetFolderByServerRelativeUrl('{_enc(parent_rel)}')/folders"
            )
            try:
                await self._api(
                    "POST",
                    url,
                    headers={"Content-Type": "application/json;odata=verbose"},
                    data=json.dumps(
                        {"__metadata": {"type": "SP.Folder"}, "ServerRelativeUrl": rel}
                    ).encode(),
                )
                log.info("Carpeta creada: %s", rel)
            except RuntimeError as exc:
                # "already exists" devuelve 409 o 500 — lo ignoramos
                if "already exists" not in str(exc).lower() and "409" not in str(exc) and "500" not in str(exc):
                    raise
        self._folders_ensured = True

    # ── StorageBackend ────────────────────────────────────────────────────────

    async def upload_event(self, event: dict) -> None:
        event_id = event["event_id"]
        filename = f"{event_id}.json"
        content  = json.dumps(event, ensure_ascii=False, indent=2).encode("utf-8")

        url = (
            f"{self._site_url}/_api/web"
            f"/GetFolderByServerRelativeUrl('{self._pending_rel}')"
            f"/Files/add(url='{quote(filename)}',overwrite=true)"
        )
        try:
            await self._api(
                "POST",
                url,
                headers={"Content-Type": "application/octet-stream"},
                data=content,
            )
        except RuntimeError as exc:
            if "404" in str(exc) or "does not exist" in str(exc).lower():
                await self._ensure_subfolders()
                await self._api(
                    "POST",
                    url,
                    headers={"Content-Type": "application/octet-stream"},
                    data=content,
                )
            else:
                raise
        log.debug("Evento subido: %s", event_id)

    async def download_snapshot(self) -> dict | None:
        url = (
            f"{self._site_url}/_api/web"
            f"/GetFileByServerRelativeUrl('{self._master_rel}/latest_snapshot.json')/$value"
        )
        result = await self._api("GET", url, headers={"Accept": "*/*"})
        if result is None:
            return None
        raw = result if isinstance(result, bytes) else result.encode()
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            log.warning("download_snapshot: JSON inválido — %s", exc)
            return None

    async def list_pending(self) -> list[str]:
        url = (
            f"{self._site_url}/_api/web"
            f"/GetFolderByServerRelativeUrl('{self._pending_rel}')"
            f"/Files?$select=Name&$orderby=Name"
        )
        result = await self._api("GET", url)
        if result is None:
            return []
        items = result.get("d", {}).get("results", [])
        return [
            item["Name"].removesuffix(".json")
            for item in items
            if item.get("Name", "").endswith(".json")
        ]

    async def mark_processed(self, event_id: str) -> None:
        filename    = f"{event_id}.json"
        source_rel  = self._pending_rel   + "/" + quote(filename)
        dest_rel    = self._processed_rel + "/" + quote(filename)
        url = (
            f"{self._site_url}/_api/web"
            f"/GetFileByServerRelativeUrl('{source_rel}')"
            f"/moveto(newurl='{dest_rel}',flags=1)"
        )
        await self._api("POST", url)
        log.debug("Evento procesado: %s", event_id)
