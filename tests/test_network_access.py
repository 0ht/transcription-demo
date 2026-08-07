import pytest

from hooks import disable_public_access, enable_public_access, network_access


@pytest.mark.unit
def test_enable_public_access_rolls_back_when_setup_fails(monkeypatch):
    resources = object()
    calls: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        enable_public_access, "load_deployment_resources", lambda: resources
    )
    monkeypatch.setattr(
        enable_public_access,
        "update_public_access",
        lambda _resources, enabled, continue_on_error=False: calls.append(
            (enabled, continue_on_error)
        ),
    )
    monkeypatch.setattr(
        enable_public_access,
        "configure_ui_registry",
        lambda _resources: (_ for _ in ()).throw(RuntimeError("registry failed")),
    )

    assert enable_public_access.main() == 1
    assert calls == [(True, False), (False, True)]


@pytest.mark.unit
def test_disable_public_access_attempts_fail_closed(monkeypatch):
    resources = object()
    calls: list[tuple[bool, bool]] = []
    monkeypatch.setattr(
        disable_public_access, "load_network_resources", lambda: resources
    )
    monkeypatch.setattr(
        disable_public_access,
        "update_public_access",
        lambda _resources, enabled, continue_on_error=False: calls.append(
            (enabled, continue_on_error)
        ),
    )

    assert disable_public_access.main() == 0
    assert calls == [(False, True)]


@pytest.mark.unit
def test_update_public_access_continues_closing_after_an_error(monkeypatch):
    resources = network_access.DeploymentResources(
        subscription_id="sub",
        resource_group="rg",
        function_name="func",
        functions_storage_name="stfunc",
        acr_name="acr",
        acr_endpoint="acr.azurecr.io",
        ui_name="ui",
    )
    updates: list[str] = []

    def fake_az(*args):
        if args[:2] == ("functionapp", "show"):
            updates.append("function")
            raise RuntimeError("function lookup failed")
        if args[:2] == ("acr", "update"):
            updates.append("acr")
            return None
        if args[:3] == ("storage", "account", "update"):
            updates.append("storage")
            return None
        raise AssertionError(f"Unexpected az invocation: {args}")

    monkeypatch.setattr(network_access, "az", fake_az)

    with pytest.raises(RuntimeError, match="Azure Functions"):
        network_access.update_public_access(
            resources,
            enabled=False,
            continue_on_error=True,
        )

    assert updates == ["function", "acr", "storage"]