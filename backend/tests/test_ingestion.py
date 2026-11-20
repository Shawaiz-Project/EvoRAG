import os
from datetime import datetime, timedelta, tzinfo
from io import BytesIO
from secrets import token_urlsafe
from typing import Any
from uuid import UUID

import pytest
import sqlalchemy as sa
from docx import Document as WordDocument
from httpx2 import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth import encode_password
from app.core.config import Settings
from app.db.identity import User
from app.db.ingestion import IngestionJob
from app.db.session import session_factory
from app.ingestion.chunking import Block, ChunkStrategy, chunk_blocks
from app.ingestion.extraction import InvalidDocument
from app.ingestion.jobs import JobRepository
from app.ingestion.remote import allowed_repo_path, public_address, safe_url
from app.ingestion.worker import IngestionWorker
from app.main import create_app

live_database = pytest.mark.skipif(
    os.getenv("EVORAG_DB_TESTS") != "1", reason="requires PostgreSQL"
)


def sample_pdf() -> bytes:
    content = b"BT /F1 12 Tf 72 720 Td (Traceable PDF material.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    data.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def fixtures() -> list[tuple[str, bytes, str, int | None, str | None]]:
    docx = WordDocument()
    docx.add_heading("Findings", 1)
    table = docx.add_table(rows=2, cols=2)
    cells = [cell for row in table.rows for cell in row.cells]
    for cell, value in zip(cells, ["Metric", "Value", "Recall", "42"], strict=True):
        cell.text = value
    docx_bytes = BytesIO()
    docx.save(docx_bytes)

    xlsx = Workbook()
    xlsx.active.title = "Results"
    xlsx.active.append(["Metric", "Value"])
    xlsx.active.append(["Precision", 24])
    xlsx_bytes = BytesIO()
    xlsx.save(xlsx_bytes)
    xlsx.close()
    return [
        ("paper.pdf", sample_pdf(), "Traceable PDF material.", 1, None),
        ("results.docx", docx_bytes.getvalue(), "Metric: Recall", None, "Findings / Table 1"),
        ("whitepaper.txt", b"Plain text test.", "Plain text test.", None, None),
        ("notes.md", b"# Evidence\nContent below.", "Content below.", None, "Evidence"),
        ("results.csv", b"Score,Value\nMRR,42\n", "Score: MRR", None, "CSV"),
        ("results.xlsx", xlsx_bytes.getvalue(), "Metric: Precision", None, "Results"),
    ]


@pytest.mark.parametrize(
    "value",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:192.168.0.10]/",
        "http://localhost/",
        "http://a.local/",
        "https://example.org:8443/",
        "https://user:pass@example.org/",
        "file:///tmp/file.txt",
    ],
)
def test_disallowed_url(value: str) -> None:
    with pytest.raises(InvalidDocument):
        safe_url(value)


def test_dns_and_repository_guards() -> None:
    assert public_address("8.8.8.8")
    assert not public_address("100.64.1.3")
    assert not public_address("64:ff9b::7f00:1")
    assert not public_address("ff02::1")
    assert not allowed_repo_path(".env")
    assert not allowed_repo_path("examples/credentials.txt")
    assert not allowed_repo_path("cache/credentials_backup.md")
    assert not allowed_repo_path("images/test.png")
    assert allowed_repo_path("src/chunks.py")


@pytest.mark.parametrize("strategy", ["recursive", "sentence", "semantic"])
def test_parent_child_chunking(strategy: ChunkStrategy) -> None:
    source = " ".join(["Fruit appears in the introduction. The second sentence adds context."] * 20)
    chunks = list(
        chunk_blocks([Block(source, 1, "Intro")], strategy, max_chars=95, parent_chars=240)
    )
    assert any(chunk.parent is not None for chunk in chunks)
    children = [chunk for chunk in chunks if chunk.parent is not None]
    for child in children:
        assert len(child.text) <= 95
        assert chunks[child.parent].block.page == 1
        assert chunks[child.parent].parent is None


@pytest.mark.anyio
@live_database
async def test_upload_worker_and_inspector(
    database: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LaggingClock(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            return datetime.now(tz) - timedelta(minutes=5)

    monkeypatch.setattr("app.ingestion.jobs.datetime", LaggingClock)
    settings = Settings().model_copy(update={"postgres_db": database.url.database})
    app = create_app(settings)
    sessions = session_factory(database)
    worker = IngestionWorker(sessions, settings)
    password = token_urlsafe(24)
    email = "ingestion@example.test"
    async with sessions.begin() as session:
        session.add(
            User(email=email, display_name="Ingestion", password_hash=encode_password(password))
        )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text

        async def upload(filename: str, data: bytes) -> dict[str, Any]:
            result = await client.post(
                "/api/v1/ingestion/files",
                params={"filename": filename},
                content=data,
                headers={"content-type": "application/octet-stream"},
            )
            assert result.status_code == 202, result.text
            assert await worker.run_once()
            finished = (await client.get(f"/api/v1/ingestion/jobs/{result.json()['id']}")).json()
            assert finished["status"] == "completed", finished
            return finished["result"]["documents"][0]

        for name, body, text, page, section in fixtures():
            record = await upload(name, body)
            doc = record["document_id"]
            version = record["version_id"]
            response = await client.get(f"/api/v1/documents/{doc}/versions/{version}")
            assert response.status_code == 200, response.text
            inspection = response.json()
            assert text in inspection["extracted_text"]
            assert inspection["version"]["attributes"]["provenance"]["filename"] == name
            matched = next(chunk for chunk in inspection["chunks"] if text in chunk["content"])
            assert (matched["page"], matched["section"]) == (page, section)

        duplicate = await upload("whitepaper.txt", b"Plain text test.")
        replacement = await upload("whitepaper.txt", b"Further revision.")
        assert duplicate["duplicate"]
        assert replacement["document_id"] == duplicate["document_id"]
        assert replacement["version"] == 2
        history = (await client.get(f"/api/v1/documents/{replacement['document_id']}")).json()
        assert [item["is_current"] for item in history["versions"]] == [True, False]
        assert len((await client.get("/api/v1/documents")).json()) == 6

        wrong_type = await client.post(
            "/api/v1/ingestion/files?filename=wrong.pdf",
            content=b"not a pdf",
            headers={"content-type": "application/octet-stream"},
        )
        assert wrong_type.status_code == 422
        unsafe = await client.post(
            "/api/v1/ingestion/remote", json={"kind": "website", "url": "http://127.0.0.1"}
        )
        assert unsafe.status_code == 422


@pytest.mark.anyio
@live_database
async def test_failed_job_retry_and_stale_claim_recovery(database: AsyncEngine) -> None:
    sessions = session_factory(database)
    async with sessions.begin() as session:
        invalid = await JobRepository(session).add(
            IngestionJob(kind="file", locator="invalid.txt", payload=b"\x00", strategy="recursive"),
        )
    worker = IngestionWorker(sessions, Settings())
    assert await worker.run_once()
    async with sessions.begin() as session:
        job = await session.get(IngestionJob, invalid.id)
        assert job.status == "failed"
        assert job.payload == b"\x00"
        assert (await JobRepository(session).retry(job.id)).status == "queued"
    async with sessions.begin() as session:
        jobs = JobRepository(session)
        claim = await jobs.claim()
        assert claim is not None
        token = claim.lease_token
        await session.execute(
            sa.update(IngestionJob)
            .where(IngestionJob.id == claim.id)
            .values(lease_until=await jobs.database_time() - timedelta(seconds=1))
        )
    async with sessions.begin() as session:
        claim = await JobRepository(session).claim()
        assert claim is not None and claim.lease_token != token
        assert isinstance(token, UUID)
        assert await JobRepository(session).lock_claim(claim.id, token) is None
