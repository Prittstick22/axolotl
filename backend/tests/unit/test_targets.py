"""Migration target tests - owner: Dev 2. Covers T-08 (unsupported target rejected)."""

import pytest
from fastapi.testclient import TestClient

from app.api.targets import MIGRATION_TARGETS, require_supported_target
from app.errors import AppError
from tests.conftest import API


def test_only_modern_python3_is_supported():
    supported = [t.target_id for t in MIGRATION_TARGETS if t.supported]
    assert supported == ["modern_python3"]


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


def test_supported_target_resolves():
    assert require_supported_target("modern_python3").target_id == "modern_python3"


@pytest.mark.parametrize(
    "target_id",
    ["fastapi", "async_python", "python_to_typescript", "python2_to_3"],
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


def test_unknown_target_is_rejected_the_same_way():
    with pytest.raises(AppError) as caught:
        require_supported_target("cobol_to_rust")
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert caught.value.status_code == 422


# --------------------------------------------------------------------------
# #21: the Plan screen needs the version, and the reasons have to fit on it
# --------------------------------------------------------------------------

def test_the_python_2_target_declares_the_version_it_targets():
    from app.api.targets import _BY_ID

    assert _BY_ID["python2_to_3"].target_python == "3.14"
    assert _BY_ID["modern_python3"].target_python == "3.12"


def test_python2_to_3_is_still_unsupported_until_the_sandbox_can_run_27():
    """Marking it supported before #22 and #23 would move the failure from a clear
    422 at planning time into a baseline that cannot run - discovered mid-demo."""
    from app.api.targets import _BY_ID

    assert _BY_ID["python2_to_3"].supported is False


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
