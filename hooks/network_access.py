import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)
ACR_READY_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 10


@dataclass(frozen=True)
class NetworkResources:
    subscription_id: str
    resource_group: str
    function_name: str
    functions_storage_name: str
    acr_name: str


@dataclass(frozen=True)
class DeploymentResources(NetworkResources):
    acr_endpoint: str
    ui_name: str


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} が設定されていません。")
    return value


def load_network_resources() -> NetworkResources:
    return NetworkResources(
        subscription_id=required_env("AZURE_SUBSCRIPTION_ID"),
        resource_group=required_env("AZURE_RESOURCE_GROUP"),
        function_name=required_env("SERVICE_FUNCTIONS_RESOURCE_NAME"),
        functions_storage_name=required_env(
            "AZURE_FUNCTIONS_STORAGE_ACCOUNT_NAME"
        ),
        acr_name=required_env("AZURE_CONTAINER_REGISTRY_NAME"),
    )


def load_deployment_resources() -> DeploymentResources:
    return DeploymentResources(
        subscription_id=required_env("AZURE_SUBSCRIPTION_ID"),
        resource_group=required_env("AZURE_RESOURCE_GROUP"),
        function_name=required_env("SERVICE_FUNCTIONS_RESOURCE_NAME"),
        functions_storage_name=required_env(
            "AZURE_FUNCTIONS_STORAGE_ACCOUNT_NAME"
        ),
        acr_name=required_env("AZURE_CONTAINER_REGISTRY_NAME"),
        acr_endpoint=required_env("AZURE_CONTAINER_REGISTRY_ENDPOINT"),
        ui_name=required_env("SERVICE_UI_RESOURCE_NAME"),
    )


def az(*args: str) -> Any:
    executable = shutil.which("az") or shutil.which("az.cmd")
    if executable is None:
        raise RuntimeError("Azure CLI (az) が PATH にありません。")

    result = subprocess.run(
        [executable, *args, "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"az {' '.join(args)} failed: {message}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def update_function_public_access(
    resources: NetworkResources, state: str
) -> None:
    function = az(
        "functionapp",
        "show",
        "--name",
        resources.function_name,
        "--resource-group",
        resources.resource_group,
        "--subscription",
        resources.subscription_id,
    )
    az(
        "resource",
        "update",
        "--ids",
        function["id"],
        "--subscription",
        resources.subscription_id,
        "--set",
        f"properties.publicNetworkAccess={state}",
    )


def update_storage_public_access(
    resources: NetworkResources, state: str, default_action: str
) -> None:
    az(
        "storage",
        "account",
        "update",
        "--name",
        resources.functions_storage_name,
        "--resource-group",
        resources.resource_group,
        "--subscription",
        resources.subscription_id,
        "--public-network-access",
        state,
        "--default-action",
        default_action,
    )


def update_public_access(
    resources: NetworkResources,
    enabled: bool,
    *,
    continue_on_error: bool = False,
) -> None:
    state = "Enabled" if enabled else "Disabled"
    acr_enabled = "true" if enabled else "false"
    default_action = "Allow" if enabled else "Deny"
    operations: list[tuple[str, Callable[[], Any]]] = [
        (
            "Azure Functions",
            lambda: update_function_public_access(resources, state),
        ),
        (
            "Azure Container Registry",
            lambda: az(
                "acr",
                "update",
                "--name",
                resources.acr_name,
                "--subscription",
                resources.subscription_id,
                "--public-network-enabled",
                acr_enabled,
                "--default-action",
                default_action,
            ),
        ),
        (
            "Functions Storage Account",
            lambda: update_storage_public_access(
                resources, state, default_action
            ),
        ),
    ]

    errors: list[str] = []
    for display_name, operation in operations:
        try:
            operation()
            LOGGER.info("%s public access: %s", display_name, state)
        except (KeyError, RuntimeError, json.JSONDecodeError) as error:
            errors.append(f"{display_name}: {error}")
            if not continue_on_error:
                raise RuntimeError(errors[-1]) from error
            LOGGER.error("%s", errors[-1])

    if errors:
        raise RuntimeError("; ".join(errors))


def configure_ui_registry(resources: DeploymentResources) -> None:
    az(
        "containerapp",
        "registry",
        "set",
        "--name",
        resources.ui_name,
        "--resource-group",
        resources.resource_group,
        "--subscription",
        resources.subscription_id,
        "--server",
        resources.acr_endpoint,
        "--identity",
        "system",
    )


def wait_for_acr(resources: DeploymentResources) -> None:
    deadline = time.monotonic() + ACR_READY_TIMEOUT_SECONDS
    url = f"https://{resources.acr_endpoint}/v2/"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        except (OSError, urllib.error.URLError) as error:
            LOGGER.info("ACR public endpoint is not reachable yet: %s", error)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if status == 401:
            LOGGER.info("ACR public endpoint is reachable.")
            return
        LOGGER.info("ACR public endpoint returned HTTP %s; retrying.", status)
        time.sleep(POLL_INTERVAL_SECONDS)

    raise RuntimeError("ACR public endpoint の到達確認がタイムアウトしました。")