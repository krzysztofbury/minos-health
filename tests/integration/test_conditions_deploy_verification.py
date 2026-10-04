from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from scripts.deploy_validation import DeployError, conditions_sample, verify_conditions
from scripts.render_validation_compose import api_service
from tests.integration.test_jgp_condition_consumers import consumer_bundle


def test_deployment_probe_checks_real_dated_conditions_not_just_catalog_points(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = consumer_bundle(tmp_path)
    sample = conditions_sample(bundle)
    assert sample is not None
    assert sample[1] == "A01"
    with TestClient(create_app(bundle)) as client:

        def get(url: str) -> tuple[int, bytes]:
            response = client.get(url)
            return response.status_code, response.content

        monkeypatch.setattr("scripts.deploy_validation.get", get)
        assert "fully_interpreted" in verify_conditions("http://testserver", sample)
        with pytest.raises(DeployError, match="planned dated source"):
            _ = verify_conditions(
                "http://testserver", (sample[0], sample[1], "foreign-source", sample[3])
            )
        with pytest.raises(DeployError, match="planned dated source"):
            _ = verify_conditions(
                "http://testserver", ("2026-01-01", sample[1], sample[2], sample[3])
            )


def test_api_startup_health_budget_covers_observed_conditions_startup(
    tmp_path: Path,
) -> None:
    service = api_service("unused", tmp_path, "a" * 64)
    health = cast(dict[str, object], service["healthcheck"])
    assert health["start_period"] == "120s"
    assert health["timeout"] == "3s"
