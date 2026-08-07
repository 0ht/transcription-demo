# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any


POLL_INTERVAL_SECONDS = 15
DELETE_TIMEOUT_SECONDS = 1800
SAL_TIMEOUT_SECONDS = 1200
AZ_READ_RETRY_ATTEMPTS = 4
AZ_READ_RETRY_DELAY_SECONDS = 2
COGNITIVE_SERVICES_API_VERSION = "2025-04-01-preview"
WEB_SITES_API_VERSION = "2024-04-01"
MANAGED_ENVIRONMENTS_API_VERSION = "2024-03-01"


def progress(message: str) -> None:
    print(message, flush=True)


def is_read_command(args: tuple[str, ...]) -> bool:
    if args[:3] == ("rest", "--method", "GET"):
        return True
    return any(arg in {"exists", "list", "list-deleted", "show"} for arg in args)


def is_transient_error(message: str) -> bool:
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "connection aborted",
            "connection reset",
            "connectionreseterror",
            "timed out",
            "timeout",
            "too many requests",
            "status code: 429",
            "status code: 500",
            "status code: 502",
            "status code: 503",
            "status code: 504",
        )
    )


def az(*args: str, allow_not_found: bool = False) -> Any:
    executable = shutil.which("az") or shutil.which("az.cmd")
    if executable is None:
        raise RuntimeError("Azure CLI (az) が PATH にありません。")

    attempts = AZ_READ_RETRY_ATTEMPTS if is_read_command(args) else 1
    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            [executable, *args, "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            if not result.stdout.strip():
                return None
            return json.loads(result.stdout)

        message = result.stderr.strip() or result.stdout.strip()
        if allow_not_found and "not found" in message.lower():
            return None
        if attempt < attempts and is_transient_error(message):
            print(
                f">>> Retrying Azure CLI read ({attempt}/{attempts - 1}): "
                f"az {' '.join(args)}",
                flush=True,
            )
            time.sleep(AZ_READ_RETRY_DELAY_SECONDS * attempt)
            continue
        raise RuntimeError(f"az {' '.join(args)} failed: {message}")

    raise RuntimeError(f"az {' '.join(args)} failed after retries")


def wait_until_deleted(resource_id: str, api_version: str) -> None:
    deadline = time.monotonic() + DELETE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        resource = az(
            "rest",
            "--method",
            "GET",
            "--uri",
            f"{resource_id}?api-version={api_version}",
            allow_not_found=True,
        )
        if resource is None:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"削除完了を確認できませんでした: {resource_id}")


def delete_function_apps(resource_group: str, subscription_id: str) -> None:
    apps = az(
        "functionapp",
        "list",
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
    )
    for app in apps or []:
        progress(f">>> Deleting Function App: {app['id']}")
        az("functionapp", "delete", "--ids", app["id"])
        wait_until_deleted(app["id"], WEB_SITES_API_VERSION)


def delete_container_apps(resource_group: str, subscription_id: str) -> None:
    apps = az(
        "containerapp",
        "list",
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
    )
    for app in apps or []:
        progress(f">>> Deleting Container App: {app['id']}")
        az("containerapp", "delete", "--ids", app["id"], "--yes")

    environments = az(
        "containerapp",
        "env",
        "list",
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
    )
    for environment in environments or []:
        environment_id = environment["id"]
        progress(f">>> Deleting Container Apps Environment: {environment_id}")
        az(
            "rest",
            "--method",
            "DELETE",
            "--uri",
            f"{environment_id}?api-version={MANAGED_ENVIRONMENTS_API_VERSION}",
        )
        progress(">>> Waiting for Container Apps Environment deletion...")
        wait_until_deleted(environment_id, MANAGED_ENVIRONMENTS_API_VERSION)


def delete_capability_hosts(parent_id: str, display_name: str) -> None:
    capability_hosts_uri = (
        f"{parent_id}/capabilityHosts"
        f"?api-version={COGNITIVE_SERVICES_API_VERSION}"
    )
    capability_hosts = az("rest", "--method", "GET", "--uri", capability_hosts_uri)
    for capability_host in capability_hosts.get("value", []):
        capability_host_id = capability_host["id"]
        progress(f">>> Deleting {display_name} capability host: {capability_host_id}")
        az(
            "rest",
            "--method",
            "DELETE",
            "--uri",
            f"{capability_host_id}?api-version={COGNITIVE_SERVICES_API_VERSION}",
        )
        wait_until_deleted(capability_host_id, COGNITIVE_SERVICES_API_VERSION)


def delete_and_purge_foundry_resources(
    resource_group: str, subscription_id: str
) -> None:
    accounts = az(
        "cognitiveservices",
        "account",
        "list",
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
    )
    for account in accounts or []:
        if account.get("kind", "").lower() != "aiservices":
            continue

        account_id = account["id"]
        account_name = account["name"]
        account_location = account["location"]
        projects_uri = (
            f"{account_id}/projects?api-version={COGNITIVE_SERVICES_API_VERSION}"
        )
        projects = az("rest", "--method", "GET", "--uri", projects_uri).get(
            "value", []
        )

        for project in projects:
            delete_capability_hosts(project["id"], f"project {project['name']}")

        delete_capability_hosts(account_id, f"account {account_name}")

        for project in projects:
            project_id = project["id"]
            progress(f">>> Deleting Foundry project: {project_id}")
            az(
                "rest",
                "--method",
                "DELETE",
                "--uri",
                f"{project_id}?api-version={COGNITIVE_SERVICES_API_VERSION}",
            )
            wait_until_deleted(project_id, COGNITIVE_SERVICES_API_VERSION)

        progress(f">>> Deleting Foundry account: {account_name}")
        az(
            "cognitiveservices",
            "account",
            "delete",
            "--name",
            account_name,
            "--resource-group",
            resource_group,
            "--subscription",
            subscription_id,
        )
        wait_until_deleted(account_id, COGNITIVE_SERVICES_API_VERSION)
        progress(f">>> Purging Foundry account: {account_name}")
        az(
            "cognitiveservices",
            "account",
            "purge",
            "--name",
            account_name,
            "--resource-group",
            resource_group,
            "--location",
            account_location,
            "--subscription",
            subscription_id,
        )

    purge_deleted_foundry_accounts(resource_group, subscription_id)


def purge_deleted_foundry_accounts(
    resource_group: str, subscription_id: str
) -> None:
    deleted_accounts = az(
        "cognitiveservices",
        "account",
        "list-deleted",
        "--subscription",
        subscription_id,
    )
    resource_group_segment = f"/resourceGroups/{resource_group}/".lower()
    for account in deleted_accounts or []:
        if resource_group_segment not in account.get("id", "").lower():
            continue
        progress(f">>> Purging soft-deleted Foundry account: {account['name']}")
        az(
            "cognitiveservices",
            "account",
            "purge",
            "--name",
            account["name"],
            "--resource-group",
            resource_group,
            "--location",
            account["location"],
            "--subscription",
            subscription_id,
        )


def get_service_association_links(
    resource_group: str, subscription_id: str
) -> list[dict[str, str]]:
    vnets = az(
        "network",
        "vnet",
        "list",
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
    )
    links: list[dict[str, str]] = []
    for vnet in vnets or []:
        subnets = az(
            "network",
            "vnet",
            "subnet",
            "list",
            "--resource-group",
            resource_group,
            "--subscription",
            subscription_id,
            "--vnet-name",
            vnet["name"],
        )
        for subnet in subnets or []:
            for link in subnet.get("serviceAssociationLinks") or []:
                links.append(
                    {
                        "vnet": vnet["name"],
                        "subnet": subnet["name"],
                        "name": link["name"],
                        "linkedResourceType": link.get("linkedResourceType", ""),
                    }
                )
    return links


def require_links_released(resource_group: str, subscription_id: str) -> None:
    deadline = time.monotonic() + SAL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        links = get_service_association_links(resource_group, subscription_id)
        if not links:
            progress(">>> All Service Association Links released.")
            return
        progress(f">>> Waiting for Service Association Links: {json.dumps(links)}")
        time.sleep(POLL_INTERVAL_SECONDS)

    links = get_service_association_links(resource_group, subscription_id)
    raise RuntimeError(
        "Service Association Link が残っています。azd down を中止します: "
        f"{json.dumps(links, ensure_ascii=False)}"
    )


def delete_private_link_scopes(resource_group: str, subscription_id: str) -> None:
    scopes = az(
        "monitor",
        "private-link-scope",
        "list",
        "--resource-group",
        resource_group,
        "--subscription",
        subscription_id,
    )
    for scope in scopes or []:
        scope_id = scope["id"]
        scope_name = scope["name"]
        scoped_resources = az(
            "monitor",
            "private-link-scope",
            "scoped-resource",
            "list",
            "--resource-group",
            resource_group,
            "--scope-name",
            scope_name,
            "--subscription",
            subscription_id,
        )
        for scoped_resource in scoped_resources or []:
            progress(f">>> Deleting AMPLS scoped resource: {scoped_resource['id']}")
            az(
                "monitor",
                "private-link-scope",
                "scoped-resource",
                "delete",
                "--ids",
                scoped_resource["id"],
                "--subscription",
                subscription_id,
                "--yes",
            )
        progress(f">>> Deleting Azure Monitor Private Link Scope: {scope_id}")
        az(
            "monitor",
            "private-link-scope",
            "delete",
            "--ids",
            scope_id,
            "--subscription",
            subscription_id,
            "--yes",
        )


def main() -> int:
    subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP")
    if not subscription_id or not resource_group:
        print(
            "ERROR: AZURE_SUBSCRIPTION_ID と AZURE_RESOURCE_GROUP が必要です。",
            file=sys.stderr,
        )
        return 1

    try:
        group = az(
            "group",
            "exists",
            "--name",
            resource_group,
            "--subscription",
            subscription_id,
        )
        if group is False:
            progress(">>> Resource group does not exist; predown cleanup is unnecessary.")
            return 0

        progress(">>> Releasing VNet integrations before azd down...")
        delete_function_apps(resource_group, subscription_id)
        delete_container_apps(resource_group, subscription_id)
        delete_and_purge_foundry_resources(resource_group, subscription_id)
        require_links_released(resource_group, subscription_id)
        delete_private_link_scopes(resource_group, subscription_id)
        progress(">>> Predown cleanup completed. azd down may proceed.")
        return 0
    except (KeyError, RuntimeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print(
            "ERROR: VNet integration was not safely released; azd down is blocked.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())