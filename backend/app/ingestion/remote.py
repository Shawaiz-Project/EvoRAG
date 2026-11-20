import base64
import ipaddress
import json
import re
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, Literal
from urllib.parse import quote

import aiohttp
from aiohttp.abc import ResolveResult
from yarl import URL

from app.ingestion.chunking import Block
from app.ingestion.extraction import InvalidDocument, bounded_blocks, decode_text, markdown_blocks

RemoteKind = Literal["website", "github"]
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
GITHUB_PATH = re.compile(r"/([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100})/?")
SHA = re.compile(r"[0-9a-f]{40}")
ALLOWED_REPO_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".c",
    ".h",
    ".cpp",
    ".sql",
}
PRIVATE_PATH = re.compile(
    r"(^|[._-])(env|secret[s]?|credentials?|password[s]?|token[s]?|private|"
    r"id_rsa|id_ed25519|settings|config|backup|fixtures?)([._-]|$)",
    re.I,
)
PRIVATE_CONTENT = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{35})"
    r"|\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*['\"][^'\"\r\n]{8,}['\"]",
    re.I,
)


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address):
        if address.scope_id is not None or address.sixtofour is not None or address.teredo:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            return False
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
    return address.is_global and not address.is_multicast and not address.is_reserved


def safe_url(value: str | URL) -> URL:
    try:
        url = URL(value)
        host = url.host
        if (
            url.scheme not in {"http", "https"}
            or not host
            or url.user is not None
            or url.password is not None
            or url.port != (443 if url.scheme == "https" else 80)
        ):
            raise InvalidDocument("Only public HTTP and HTTPS URLs on standard ports are supported")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if "." not in host.rstrip(".") or host.rstrip(".").lower().endswith(
                (".localhost", ".local", ".internal", ".home.arpa")
            ):
                raise InvalidDocument("Local network hostnames are unsupported") from None
        else:
            if not public_address(host):
                raise InvalidDocument("Private or special network addresses are unsupported")
        return url.with_fragment(None)
    except ValueError as error:
        if isinstance(error, InvalidDocument):
            raise
        raise InvalidDocument("Malformed URL") from error


class PublicDNS(aiohttp.resolver.ThreadedResolver):
    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        results = await super().resolve(host, port, family)
        if not results or any(not public_address(result["host"]) for result in results):
            raise OSError("DNS resolved to a prohibited address")
        return results


@dataclass(frozen=True, slots=True)
class Response:
    url: URL
    mime: str
    charset: str | None
    body: bytes


async def fetch_public(value: str, limit: int, *, github: bool = False) -> Response:
    url = safe_url(value)
    headers = {"Accept-Encoding": "identity", "User-Agent": "EvoRAG-ingestion/1"}
    if github:
        headers["Accept"] = "application/vnd.github+json"
    connector = aiohttp.TCPConnector(resolver=PublicDNS(), use_dns_cache=False)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=aiohttp.ClientTimeout(total=15, sock_connect=5),
        trust_env=False,
        auto_decompress=False,
        cookie_jar=aiohttp.DummyCookieJar(),
        headers=headers,
    ) as client:
        for _ in range(6):
            if github and (url.scheme != "https" or url.host != "api.github.com"):
                raise InvalidDocument("GitHub requests must stay on the official API host")
            async with client.get(url, allow_redirects=False) as response:
                if response.status in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if location is None:
                        raise InvalidDocument("Remote redirect omitted its destination")
                    url = safe_url(url.join(URL(location)))
                    continue
                if response.status != 200:
                    raise InvalidDocument(f"Remote server returned HTTP {response.status}")
                if (response.content_length or 0) > limit:
                    raise InvalidDocument("Remote input exceeds the size limit")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise InvalidDocument("Remote server ignored the uncompressed response request")
                body = bytearray()
                async for part in response.content.iter_chunked(65536):
                    body.extend(part)
                    if len(body) > limit:
                        raise InvalidDocument("Remote input exceeds the size limit")
                return Response(url, response.content_type, response.charset, bytes(body))
    raise InvalidDocument("Remote server exceeded the redirect limit")


class VisibleHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.buffer: list[str] = []
        self.heading: list[str] | None = None
        self.section: str | None = None
        self.hidden: list[str] = []

    def flush(self) -> None:
        text = " ".join(" ".join(self.buffer).split())
        if text:
            self.blocks.append(Block(text, section=self.section))
        self.buffer.clear()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"head", "script", "style", "noscript", "template", "svg"}:
            self.hidden.append(tag)
        elif not self.hidden and re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.heading = []
        elif not self.hidden and self.heading is None and tag in {"p", "li", "tr", "br", "pre"}:
            self.buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
            return
        if self.heading is not None and re.fullmatch(r"h[1-6]", tag):
            self.section = " ".join(" ".join(self.heading).split())
            self.buffer.append(self.section)
            self.heading = None

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            target = self.heading if self.heading is not None else self.buffer
            target.append(data)


@dataclass(frozen=True, slots=True)
class RemoteDocument:
    locator: str
    title: str
    mime: str
    data: bytes
    blocks: list[Block]
    provenance: dict[str, str]


async def website(url: str, limit: int) -> RemoteDocument:
    response = await fetch_public(url, limit)
    if response.mime not in {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}:
        raise InvalidDocument("Website response is not HTML or plain text")
    try:
        text = response.body.decode(response.charset or "utf-8", errors="replace").replace(
            "\x00", ""
        )
    except LookupError as error:
        raise InvalidDocument("Website declared an unknown encoding") from error
    if response.mime in {"text/html", "application/xhtml+xml"}:
        parser = VisibleHTML()
        parser.feed(text)
        parser.close()
        parser.flush()
        blocks: Iterator[Block] = iter(parser.blocks)
    elif response.mime == "text/markdown":
        blocks = markdown_blocks(text)
    else:
        blocks = iter((Block(text),))
    return RemoteDocument(
        url,
        response.url.host or url,
        response.mime,
        response.body,
        bounded_blocks(blocks),
        {"requested_url": url, "resolved_url": str(response.url)},
    )


def github_location(value: str) -> tuple[str, str]:
    url = safe_url(value)
    match = GITHUB_PATH.fullmatch(url.path)
    if url.scheme != "https" or url.host != "github.com" or url.query or match is None:
        raise InvalidDocument("Use https://github.com/OWNER/REPOSITORY")
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if repo in {"", ".", ".."}:
        raise InvalidDocument("Invalid GitHub repository name")
    return owner, repo


def allowed_repo_path(value: str) -> bool:
    path = PurePosixPath(value)
    if path.is_absolute() or path.suffix.lower() not in ALLOWED_REPO_EXTENSIONS:
        return False
    return not any(
        part.startswith(".")
        or part.lower() in {"vendor", "node_modules", "dist", "build", "secrets"}
        or PRIVATE_PATH.search(part)
        for part in path.parts
    )


async def github_json(path: str, limit: int) -> dict[str, Any]:
    response = await fetch_public(f"https://api.github.com{path}", limit, github=True)
    result = json.loads(response.body)
    if not isinstance(result, dict):
        raise InvalidDocument("Invalid GitHub API response")
    return result


async def github(value: str, limit: int) -> list[RemoteDocument]:
    owner, repo = github_location(value)
    repository = f"/repos/{owner}/{repo}"
    metadata = await github_json(repository, limit)
    default_branch = quote(metadata["default_branch"], safe="")
    reference = await github_json(f"{repository}/commits/{default_branch}", limit)
    commit = reference["sha"]
    if not SHA.fullmatch(commit):
        raise InvalidDocument("GitHub returned an invalid commit identifier")
    tree = await github_json(f"{repository}/git/trees/{commit}?recursive=1", limit)
    if tree.get("truncated"):
        raise InvalidDocument("GitHub repository tree exceeds the processing limit")
    documents: list[RemoteDocument] = []
    remaining = limit
    examined = 0
    for item in sorted(tree["tree"], key=lambda entry: entry["path"]):
        path = item["path"]
        size = item.get("size", 0)
        if (
            item["type"] != "blob"
            or item["mode"] not in {"100644", "100755"}
            or not allowed_repo_path(path)
            or not 0 < size <= min(remaining, 256_000)
        ):
            continue
        examined += 1
        if examined > 40:
            break
        sha = item["sha"]
        if not SHA.fullmatch(sha):
            continue
        blob = await github_json(f"{repository}/git/blobs/{sha}", min(size * 2 + 8192, limit))
        if blob.get("encoding") != "base64":
            continue
        data = base64.b64decode("".join(blob["content"].splitlines()), validate=True)
        remaining -= len(data)
        if len(data) != size or remaining < 0:
            raise InvalidDocument("GitHub blob size did not match its manifest")
        try:
            text = decode_text(data)
            if PRIVATE_CONTENT.search(text):
                continue
            markdown = PurePosixPath(path).suffix.lower() in {".md", ".markdown"}
            blocks = bounded_blocks(
                markdown_blocks(text) if markdown else [Block(text, section=path)]
            )
        except InvalidDocument:
            continue
        documents.append(
            RemoteDocument(
                path,
                path,
                "text/markdown" if markdown else "text/plain",
                data,
                blocks,
                {"repository": value, "commit": commit, "path": path, "blob": sha},
            )
        )
    if not documents:
        raise InvalidDocument("No allowlisted text files were found in the repository")
    return documents
