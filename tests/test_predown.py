import json
import subprocess

import pytest

from hooks import predown


@pytest.mark.unit
def test_az_retries_transient_read_failure(monkeypatch):
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="",
                stderr="ConnectionResetError: connection aborted",
            )
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"state": "Deleted"}),
            stderr="",
        )

    sleeps: list[int] = []
    monkeypatch.setattr(predown.shutil, "which", lambda _name: "az")
    monkeypatch.setattr(predown.subprocess, "run", fake_run)
    monkeypatch.setattr(predown.time, "sleep", sleeps.append)

    result = predown.az("rest", "--method", "GET", "--uri", "resource")

    assert result == {"state": "Deleted"}
    assert calls == 2
    assert sleeps == [predown.AZ_READ_RETRY_DELAY_SECONDS]


@pytest.mark.unit
def test_az_does_not_retry_delete_failure(monkeypatch):
    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="ConnectionResetError: connection aborted",
        )

    monkeypatch.setattr(predown.shutil, "which", lambda _name: "az")
    monkeypatch.setattr(predown.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="ConnectionResetError"):
        predown.az("rest", "--method", "DELETE", "--uri", "resource")

    assert calls == 1


@pytest.mark.unit
def test_foundry_cleanup_deletes_resources_in_dependency_order(monkeypatch):
    calls: list[str] = []
    account_id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/ai"
    project_id = f"{account_id}/projects/project"

    def fake_az(*args, **_kwargs):
        if args[:3] == ("cognitiveservices", "account", "list"):
            return [
                {
                    "id": account_id,
                    "kind": "AIServices",
                    "location": "japaneast",
                    "name": "ai",
                }
            ]
        if args[:3] == ("rest", "--method", "GET"):
            return {"value": [{"id": project_id, "name": "project"}]}
        if args[:3] == ("rest", "--method", "DELETE"):
            calls.append("project-delete")
            return None
        if args[:3] == ("cognitiveservices", "account", "delete"):
            calls.append("account-delete")
            return None
        if args[:3] == ("cognitiveservices", "account", "purge"):
            calls.append("account-purge")
            return None
        if args[:3] == ("cognitiveservices", "account", "list-deleted"):
            return []
        raise AssertionError(f"Unexpected az invocation: {args}")

    monkeypatch.setattr(predown, "az", fake_az)
    monkeypatch.setattr(
        predown,
        "delete_capability_hosts",
        lambda parent_id, _name: calls.append(
            "project-host" if parent_id == project_id else "account-host"
        ),
    )
    monkeypatch.setattr(predown, "wait_until_deleted", lambda *_: None)

    predown.delete_and_purge_foundry_resources("rg", "sub")

    assert calls == [
        "project-host",
        "account-host",
        "project-delete",
        "account-delete",
        "account-purge",
    ]


@pytest.mark.unit
def test_require_links_released_returns_when_no_links(monkeypatch):
    monkeypatch.setattr(predown, "get_service_association_links", lambda *_: [])

    predown.require_links_released("test-rg", "test-subscription")


@pytest.mark.unit
def test_delete_function_apps_waits_for_deletion(monkeypatch):
    function_id = "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Web/sites/func"
    calls: list[tuple] = []

    def fake_az(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("functionapp", "list"):
            return [{"id": function_id}]
        if args[:2] == ("functionapp", "delete"):
            return None
        raise AssertionError(f"Unexpected az invocation: {args}")

    waits: list[tuple[str, str]] = []
    monkeypatch.setattr(predown, "az", fake_az)
    monkeypatch.setattr(
        predown,
        "wait_until_deleted",
        lambda resource_id, api_version: waits.append((resource_id, api_version)),
    )

    predown.delete_function_apps("rg", "sub")

    assert calls[1] == ("functionapp", "delete", "--ids", function_id)
    assert waits == [(function_id, predown.WEB_SITES_API_VERSION)]


@pytest.mark.unit
def test_delete_container_environment_uses_single_rest_delete_then_waits(monkeypatch):
    environment_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/"
        "Microsoft.App/managedEnvironments/cae"
    )
    calls: list[tuple] = []

    def fake_az(*args, **_kwargs):
        calls.append(args)
        if args[:2] == ("containerapp", "list"):
            return []
        if args[:3] == ("containerapp", "env", "list"):
            return [{"id": environment_id}]
        if args[:3] == ("rest", "--method", "DELETE"):
            return None
        raise AssertionError(f"Unexpected az invocation: {args}")

    waits: list[tuple[str, str]] = []
    monkeypatch.setattr(predown, "az", fake_az)
    monkeypatch.setattr(
        predown,
        "wait_until_deleted",
        lambda resource_id, api_version: waits.append((resource_id, api_version)),
    )

    predown.delete_container_apps("rg", "sub")

    assert calls[-1] == (
        "rest",
        "--method",
        "DELETE",
        "--uri",
        f"{environment_id}?api-version={predown.MANAGED_ENVIRONMENTS_API_VERSION}",
    )
    assert waits == [
        (environment_id, predown.MANAGED_ENVIRONMENTS_API_VERSION)
    ]


@pytest.mark.unit
def test_require_links_released_raises_when_link_remains(monkeypatch):
    remaining_link = {
        "vnet": "test-vnet",
        "subnet": "test-subnet",
        "name": "legionservicelink",
        "linkedResourceType": "Microsoft.App/environments",
    }
    monkeypatch.setattr(
        predown,
        "get_service_association_links",
        lambda *_: [remaining_link],
    )
    monkeypatch.setattr(predown, "SAL_TIMEOUT_SECONDS", 0)

    with pytest.raises(RuntimeError, match="azd down を中止します"):
        predown.require_links_released("test-rg", "test-subscription")


@pytest.mark.unit
def test_main_blocks_teardown_before_ampls_delete_when_link_remains(monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "test-rg")
    monkeypatch.setattr(predown, "az", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        predown,
        "delete_function_apps",
        lambda *_: calls.append("functions"),
    )
    monkeypatch.setattr(
        predown,
        "delete_container_apps",
        lambda *_: calls.append("container-apps"),
    )
    monkeypatch.setattr(
        predown,
        "delete_and_purge_foundry_resources",
        lambda *_: calls.append("foundry"),
    )

    def fail_on_remaining_link(*_args):
        calls.append("links")
        raise RuntimeError("Service Association Link が残っています")

    monkeypatch.setattr(predown, "require_links_released", fail_on_remaining_link)
    monkeypatch.setattr(
        predown,
        "delete_private_link_scopes",
        lambda *_: calls.append("ampls"),
    )

    result = predown.main()

    assert result == 1
    assert calls == ["functions", "container-apps", "foundry", "links"]


@pytest.mark.unit
def test_main_allows_teardown_after_links_are_released(monkeypatch):
    calls: list[str] = []
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "test-subscription")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "test-rg")
    monkeypatch.setattr(predown, "az", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        predown,
        "delete_function_apps",
        lambda *_: calls.append("functions"),
    )
    monkeypatch.setattr(
        predown,
        "delete_container_apps",
        lambda *_: calls.append("container-apps"),
    )
    monkeypatch.setattr(
        predown,
        "delete_and_purge_foundry_resources",
        lambda *_: calls.append("foundry"),
    )
    monkeypatch.setattr(
        predown,
        "require_links_released",
        lambda *_: calls.append("links"),
    )
    monkeypatch.setattr(
        predown,
        "delete_private_link_scopes",
        lambda *_: calls.append("ampls"),
    )

    result = predown.main()

    assert result == 0
    assert calls == ["functions", "container-apps", "foundry", "links", "ampls"]