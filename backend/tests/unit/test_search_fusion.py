"""Hybrid fusion and honest labelling - owner: Dev 3. Offline, no Atlas, no Gemini.

Atlas is replaced by a fake collection that reads the aggregation pipeline and answers
according to its first stage. That is enough to test the parts most likely to be
wrong and least likely to be noticed:

  - the fusion arithmetic, and the order it produces
  - `native_hybrid`, which must say what actually ran rather than what was hoped for
  - a missing index returning 503, never an empty hit list that reads as "no matches"
"""

from typing import Any

import pytest

from app import search
from app.errors import AppError
from app.search import RRF_K

pytestmark = pytest.mark.anyio


def chunk(name: str, **extra: Any) -> dict[str, Any]:
    row = {
        "chunk_id": f"chunk_{name}",
        "symbol_id": f"sym_{name}",
        "module_id": "pricing",
        "qualified_name": f"pricing.{name}",
        "path": "pricing.py",
        "start_line": 1,
        "end_line": 9,
        "snapshot_hash": "sha256:abc",
        "source": f"def {name}(): ...",
        "description": None,
        "score": 1.0,
    }
    row.update(extra)
    return row


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield dict(row)
        return gen()


class FakeCollection:
    """Answers by first pipeline stage, and records what it was asked."""

    def __init__(self, keyword: list[dict], vector: list[dict],
                 fused: list[dict] | None = None,
                 rank_fusion_fails: bool = False) -> None:
        self.keyword = keyword
        self.vector = vector
        self.fused = fused or []
        self.rank_fusion_fails = rank_fusion_fails
        self.pipelines: list[list[dict]] = []

    async def aggregate(self, pipeline: list[dict]) -> FakeCursor:
        self.pipelines.append(pipeline)
        stage = next(iter(pipeline[0]))
        if stage == "$search":
            return FakeCursor(self.keyword)
        if stage == "$vectorSearch":
            return FakeCursor(self.vector)
        if stage == "$rankFusion":
            if self.rank_fusion_fails:
                raise RuntimeError("rankFusion is not supported on this cluster")
            return FakeCursor(self.fused)
        raise AssertionError(f"unexpected stage {stage}")


@pytest.fixture
def atlas(monkeypatch):
    """Fake collection, with the search layer's own dependencies made local."""
    state: dict[str, Any] = {
        "collection": None,
        "status": {"keyword": "ok", "vector": "ok"},
        "native": True,
        "embed_fails": False,
    }

    class FakeDatabase:
        def __getitem__(self, _name: str) -> FakeCollection:
            return state["collection"]

    async def get_db():
        return FakeDatabase()

    async def search_status():
        return state["status"]

    async def supports_native_hybrid():
        return state["native"]

    async def embed_one(_text: str):
        if state["embed_fails"]:
            from app.search.embeddings import EmbeddingUnavailable

            raise EmbeddingUnavailable("no key")
        return [0.1] * 768

    # No indexing status recorded: these repositories were never indexed in the
    # background, so search behaves as before #28 (test_background_indexing.py
    # covers the gate itself).
    async def no_indexing_status(_repo_id: str):
        return None

    from app.persistence import db
    from app.search import progress

    monkeypatch.setattr(db, "get_db", get_db)
    monkeypatch.setattr(progress, "get", no_indexing_status)
    monkeypatch.setattr(search, "search_status", search_status)
    monkeypatch.setattr(search, "supports_native_hybrid", supports_native_hybrid)
    monkeypatch.setattr(search, "embed_one", embed_one)
    return state


async def test_keyword_mode_returns_ranked_hits(atlas):
    atlas["collection"] = FakeCollection(
        keyword=[chunk("finalise_amount"), chunk("line_gross")], vector=[]
    )
    result = await search.search_repository("repo_1", "finalise_amount", mode="keyword")

    assert result["mode_used"] == "keyword"
    assert result["native_hybrid"] is False
    assert [h.qualified_name for h in result["hits"]] == [
        "pricing.finalise_amount", "pricing.line_gross"
    ]
    assert [h.rank for h in result["hits"]] == [1, 2]
    assert result["hits"][0].keyword_rank == 1
    assert result["hits"][0].vector_rank is None


async def test_the_keyword_query_is_filtered_to_one_repository(atlas):
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[])
    await search.search_repository("repo_1", "x", mode="keyword")

    compound = atlas["collection"].pipelines[0][0]["$search"]["compound"]
    assert compound["filter"] == [
        {"equals": {"path": "repository_id", "value": "repo_1"}}
    ]


async def test_an_exact_identifier_outranks_a_passing_mention(atlas):
    """T-06 depends on this boost ordering, so pin it."""
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[])
    await search.search_repository("repo_1", "x", mode="keyword")

    should = atlas["collection"].pipelines[0][0]["$search"]["compound"]["should"]

    def label(clause: dict) -> str:
        operator = next(iter(clause))
        path = clause[operator]["path"]
        if isinstance(path, dict):
            path = f"{path['value']}.{path['multi']}"
        return path if operator == "text" else f"{path}.{operator}"

    boosts = {
        label(clause): clause[next(iter(clause))].get("score", {})
        .get("boost", {}).get("value", 1)
        for clause in should
    }
    # The exact-analysed name beats the prose-analysed one, which beats everything
    # else, down to the raw body last.
    assert boosts["qualified_name.exact"] > boosts["qualified_name"]
    assert boosts["qualified_name"] > boosts["identifiers"] > boosts["description"]
    assert boosts["description"] > boosts["source"]
    # An exact phrase in the body outranks the query's words in a name, so an error
    # string finds the function that raises it; an exact name still wins overall.
    assert boosts["qualified_name.exact"] > boosts["source.phrase"]
    assert boosts["source.phrase"] > boosts["qualified_name"]


async def test_embeddings_never_reach_the_client(atlas):
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[])
    await search.search_repository("repo_1", "x", mode="keyword")

    projection = atlas["collection"].pipelines[0][-1]["$project"]
    assert projection["embedding"] == 0
    assert projection["_id"] == 0


async def test_native_hybrid_is_claimed_only_when_rank_fusion_actually_ran(atlas):
    atlas["collection"] = FakeCollection(
        keyword=[chunk("a")], vector=[chunk("b")], fused=[chunk("a"), chunk("b")]
    )
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    assert result["mode_used"] == "hybrid"
    assert result["native_hybrid"] is True
    assert {next(iter(p[0])) for p in atlas["collection"].pipelines} == {"$rankFusion"}


async def test_a_failing_rank_fusion_falls_back_without_claiming_native(atlas):
    """The fallback is honest or it is worse than useless."""
    atlas["collection"] = FakeCollection(
        keyword=[chunk("a")], vector=[chunk("b")], rank_fusion_fails=True
    )
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    assert result["mode_used"] == "hybrid"
    assert result["native_hybrid"] is False
    assert {h.qualified_name for h in result["hits"]} == {"pricing.a", "pricing.b"}


async def test_an_older_cluster_uses_python_fusion_and_says_so(atlas):
    atlas["native"] = False
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[chunk("a")])
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    assert result["native_hybrid"] is False
    assert [next(iter(p[0])) for p in atlas["collection"].pipelines] == [
        "$search", "$vectorSearch"
    ]


async def test_python_fusion_ranks_a_chunk_both_engines_found_above_either_alone(atlas):
    """The whole point of fusion: agreement beats a single strong opinion."""
    atlas["native"] = False
    atlas["collection"] = FakeCollection(
        keyword=[chunk("only_keyword"), chunk("both")],
        vector=[chunk("only_vector"), chunk("both")],
    )
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    hits = result["hits"]
    assert hits[0].qualified_name == "pricing.both"
    assert hits[0].keyword_rank == 2
    assert hits[0].vector_rank == 2
    # Scores are rounded for display; the ordering above is computed unrounded.
    assert hits[0].score == round(2.0 / (RRF_K + 2), 6)
    # Found by one engine only: one reciprocal term, and the better rank first.
    assert {h.qualified_name for h in hits[1:]} == {
        "pricing.only_keyword", "pricing.only_vector"
    }
    assert hits[1].score == round(1.0 / (RRF_K + 1), 6)


async def test_fused_hits_are_labelled_hybrid_not_whichever_engine_found_them(atlas):
    atlas["native"] = False
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[chunk("b")])
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    assert {h.match_method for h in result["hits"]} == {"hybrid"}
    assert [h.rank for h in result["hits"]] == [1, 2]


async def test_a_building_index_is_a_503_not_an_empty_result(atlas):
    """An empty list would read as "nothing matched", a different answer entirely."""
    atlas["status"] = {"keyword": "building", "vector": "building"}
    atlas["collection"] = FakeCollection(keyword=[], vector=[])

    with pytest.raises(AppError) as caught:
        await search.search_repository("repo_1", "rounding", mode="keyword")

    assert caught.value.status_code == 503
    assert caught.value.code == "SEARCH_INDEX_NOT_READY"
    assert "still building" in caught.value.message


async def test_a_missing_vector_index_degrades_to_keyword_and_says_which_ran(atlas):
    atlas["status"] = {"keyword": "ok", "vector": "unavailable"}
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[])
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    assert result["mode_used"] == "keyword"
    assert result["native_hybrid"] is False
    assert result["hits"][0].qualified_name == "pricing.a"


async def test_hybrid_falls_back_to_keyword_when_the_query_cannot_be_embedded(atlas):
    atlas["embed_fails"] = True
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[])
    result = await search.search_repository("repo_1", "rounding", mode="hybrid")

    assert result["mode_used"] == "keyword"
    assert result["hits"][0].qualified_name == "pricing.a"


async def test_vector_mode_fails_loudly_when_the_query_cannot_be_embedded(atlas):
    """Degrading silently in explicit vector mode would answer a different question."""
    atlas["embed_fails"] = True
    atlas["collection"] = FakeCollection(keyword=[], vector=[])

    with pytest.raises(AppError) as caught:
        await search.search_repository("repo_1", "rounding", mode="vector")
    assert caught.value.status_code == 503


async def test_an_empty_query_is_rejected(atlas):
    atlas["collection"] = FakeCollection(keyword=[], vector=[])
    with pytest.raises(AppError) as caught:
        await search.search_repository("repo_1", "   ", mode="keyword")
    assert caught.value.status_code == 422


async def test_limit_is_clamped(atlas):
    atlas["collection"] = FakeCollection(keyword=[chunk("a")], vector=[])
    await search.search_repository("repo_1", "x", mode="keyword", limit=10_000)
    assert atlas["collection"].pipelines[0][1]["$limit"] == 50


def test_rank_breakdown_survives_a_shape_it_does_not_recognise():
    from app.search import _ranks_from_details

    assert _ranks_from_details(None) == (None, None)
    assert _ranks_from_details({"details": [{"nonsense": 1}]}) == (None, None)
    assert _ranks_from_details({
        "details": [
            {"inputPipelineName": "keyword", "rank": 3},
            {"inputPipelineName": "vector", "rank": 7},
        ]
    }) == (3, 7)
