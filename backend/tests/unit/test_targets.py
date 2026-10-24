"""Migration target tests - owner: Dev 2. Covers T-08 (unsupported target rejected)."""

import pytest
from fastapi.testclient import TestClient

from app.api.targets import MIGRATION_TARGETS, require_supported_target
from app.errors import AppError
from tests.conftest import API


def test_the_two_implemented_python_targets_are_supported():
    supported = [t.target_id for t in MIGRATION_TARGETS if t.supported]
    assert supported == ["modern_python3", "python2_to_3"]


def test_every_unsupported_target_explains_itself():
    """The UI shows these disabled with a reason, so the reason must exist."""
    for target in MIGRATION_TARGETS:
        if not target.supported:
            assert target.unsupported_reason, target.target_id


def test_endpoint_lists_every_target(client: TestClient):
    response = client.get(f"{API}/migration-targets")
    assert response.status_code == 200

    body = response.json()
    assert {t["target_id"] for t in body} == {
        "modern_python3", "fastapi", "async_python",
        "python_to_typescript", "python2_to_3",
    }
    assert set(body[0]) == {
        "target_id", "label", "supported", "description", "unsupported_reason",
        # Additive under §11 (#21): the Plan screen shows the target version instead
        # of hardcoding "Python 3.14" in the page.
        "target_python",
    }


@pytest.mark.parametrize("target_id", ["modern_python3", "python2_to_3"])
def test_supported_target_resolves(target_id):
    assert require_supported_target(target_id).target_id == target_id


@pytest.mark.parametrize(
    "target_id",
    ["fastapi", "async_python", "python_to_typescript"],
)
def test_unsupported_target_is_rejected_with_422(target_id):
    """T-08: disabled or rejected at the backend with a clear error."""
    with pytest.raises(AppError) as caught:
        require_supported_target(target_id)

    error = caught.value
    assert error.code == "UNSUPPORTED_TARGET"
    assert error.status_code == 422
    assert error.details["target_id"] == target_id
    assert error.details["reason"]
    assert error.details["supported"] == ["modern_python3", "python2_to_3"]


def test_unknown_target_is_rejected_the_same_way():
    with pytest.raises(AppError) as caught:
        require_supported_target("cobol_to_rust")
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert caught.value.status_code == 422
    assert caught.value.details["supported"] == ["modern_python3", "python2_to_3"]


# --------------------------------------------------------------------------
# #21: the Plan screen needs the version, and the reasons have to fit on it
# --------------------------------------------------------------------------

def test_the_python_2_target_declares_the_version_it_targets():
    from app.api.targets import _BY_ID

    assert _BY_ID["python2_to_3"].target_python == "3.14"
    assert _BY_ID["modern_python3"].target_python == "3.12"


def test_the_ui_receives_an_enabled_chess_target_and_the_real_runtime_pair(client):
    """#22 and #23 are merged: UI selection must reach the 2.7/3.14 verifier."""
    from app.sandbox.runtimes import for_target

    response = client.get(f"{API}/migration-targets")
    target = next(t for t in response.json() if t["target_id"] == "python2_to_3")
    assert target["supported"] is True
    assert target["unsupported_reason"] is None
    assert target["target_python"] == "3.14"
    pair = for_target(target["target_id"])
    assert (pair.baseline.python, pair.candidate.python) == ("2.7", "3.14")


def test_every_unsupported_reason_is_one_short_line():
    """Dev 5: at 1280x720 the longer reasons took two to four lines each and pushed
    the target column below the fold."""
    from app.api.targets import MIGRATION_TARGETS

    for target in MIGRATION_TARGETS:
        if target.supported:
            continue
        reason = target.unsupported_reason
        assert reason, f"{target.target_id} is unsupported without saying why"
        assert "\n" not in reason
        assert len(reason) <= 70, f"{target.target_id}: {len(reason)} characters"
