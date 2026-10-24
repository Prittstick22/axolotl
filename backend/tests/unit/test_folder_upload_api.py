"""POST /repositories/import with a folder: multipart `files` plus a `paths` entry each.

Ingestion is stubbed: these tests are about the HTTP shape, which the pipeline tests
cannot see. The safety rules themselves are covered in test_ingestion_safety.py.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import ingestion
from app.api.repositories import _spool_zip
from app.errors import AppError
from app.ingestion.safety import MAX_ARCHIVE_BYTES, MAX_EXTRACTED_BYTES
from app.main import create_app
from app.models.contracts import RepositoryRecord

URL = "/api/v1/repositories/import"


@pytest.fixture
def received(monkeypatch):
    calls: list[dict] = []

    async def import_repository(**kwargs):
        calls.append(kwargs)
        return RepositoryRecord(
            repository_id="repo_test", name=kwargs.get("name") or "x",
            snapshot_hash="sha256:0", origin="folder_upload", executable=False,
            file_count=1, module_count=1, symbol_count=0, test_file_count=0,
            analysis_status="ok", created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(ingestion, "import_repository", import_repository)
    return calls


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


def test_each_file_arrives_with_its_path_inside_the_folder(client, received):
    response = client.post(
        URL,
        files=[
            ("files", ("setup.py", b"setup()\n")),
            ("files", ("board.py", b"BOARD = 1\n")),
        ],
        data={"paths": ["setup.py", "local_chess/board.py"], "name": "chess"},
    )

    assert response.status_code == 200, response.text
    assert received == [{
        "folder": [("setup.py", b"setup()\n"), ("local_chess/board.py", b"BOARD = 1\n")],
        "name": "chess",
    }]


def test_a_file_without_a_path_is_refused_before_import(client, received):
    response = client.post(
        URL,
        files=[("files", ("a.py", b"")), ("files", ("b.py", b""))],
        data={"paths": ["a.py"]},
    )

    assert response.status_code == 422
    assert received == []


def test_an_oversized_folder_is_refused_before_import(client, received):
    response = client.post(
        URL,
        files=[("files", ("big.py", b"x" * (MAX_EXTRACTED_BYTES + 1)))],
        data={"paths": ["big.py"]},
    )

    assert response.status_code == 422
    assert received == []


def test_a_folder_cannot_be_sent_with_a_fixture(client, received):
    response = client.post(
        URL,
        files=[("files", ("a.py", b""))],
        data={"paths": ["a.py"], "demo_fixture_id": "legacy_orders"},
    )

    assert response.status_code == 422
    assert received == []


def test_a_zip_upload_keeps_its_project_name(client, received):
    """Regression: a shared Form() default bound `name` to `demo_fixture_id`."""
    response = client.post(
        URL, files={"file": ("chess.zip", b"PK")}, data={"name": "my-project"}
    )

    assert response.status_code == 200, response.text
    assert received[0]["name"] == "my-project"


def test_an_oversized_zip_is_refused_before_import(client, received):
    response = client.post(
        URL, files={"file": ("big.zip", b"x" * (MAX_ARCHIVE_BYTES + 1))}
    )

    assert response.status_code == 422
    assert "larger than 10 MB" in response.text
    assert received == []


async def test_a_zip_upload_is_copied_in_bounded_chunks(tmp_path):
    """An endless upload is cut off at the limit, not read whole."""
    reads: list[int] = []

    class Endless:
        async def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return b"x" * 1024 * 1024

    with pytest.raises(AppError, match="larger than 10 MB"):
        await _spool_zip(Endless(), tmp_path / "big.zip")  # type: ignore[arg-type]

    assert all(size > 0 for size in reads)
    assert len(reads) <= MAX_ARCHIVE_BYTES // (1024 * 1024) + 1
