"""Migration target tests - owner: Dev 2. Covers T-08 (unsupported target rejected)."""

import pytest
from fastapi.testclient import TestClient

from app.api.targets import MIGRATION_TARGETS, require_supported_target
from app.errors import AppError
from tests.conftest import API


def test_the_two_supported_targets_are_the_two_the_sandbox_can_run():
    """`python2_to_3` joined on 24 Oct, once #22 gave the sandbox a 2.7 and a 3.14
    image and #23 gave chess behaviour cases. Everything else stays disabled with a
    reason, which is what the Plan screen shows."""
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


def test_supported_target_resolves():
    assert require_supported_target("modern_python3").target_id == "modern_python3"


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


def test_python2_to_3_is_supported_now_that_both_halves_exist():
    """It was deliberately `False` until 24 Oct, because marking it supported before
    #22 and #23 would have moved the failure from a clear 422 at planning time into a
    baseline that could not run - discovered mid-demo rather than mid-sentence.

    Both halves now exist, so the two things that made it false are asserted here
    instead: if either is removed, this fails rather than the demo doing it.
    """
    from app.api.targets import _BY_ID

    target = _BY_ID["python2_to_3"]
    assert target.supported is True
    assert target.unsupported_reason is None, "a supported target needs no excuse"
    assert target.target_python == "3.14"


def test_the_sandbox_has_an_image_for_both_sides_of_a_python_2_migration():
    """Half of why `python2_to_3` is supported (#22). Without both images the
    baseline or the candidate cannot run."""
    from app.config import get_settings

    settings = get_settings()
    assert settings.runner_image_py27
    assert settings.runner_image_py314
    assert settings.runner_image_py27 != settings.runner_image_py314


def test_chess_has_its_own_held_out_suite():
    """The other half (#23). A target that can be planned but not verified is worse
    than one that is honestly unsupported."""
    from app.verification.cases import HELD_OUT_BY_FIXTURE

    assert "chess" in HELD_OUT_BY_FIXTURE
    assert HELD_OUT_BY_FIXTURE["chess"] != HELD_OUT_BY_FIXTURE["legacy_orders"], (
        "chess would be verified against the orders cases"
    )


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
