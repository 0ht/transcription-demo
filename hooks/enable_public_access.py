# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import logging

from network_access import (
    configure_ui_registry,
    load_deployment_resources,
    update_public_access,
    wait_for_acr,
)


logging.basicConfig(level=logging.INFO, format=">>> %(message)s")
LOGGER = logging.getLogger(__name__)


def main() -> int:
    try:
        resources = load_deployment_resources()
        LOGGER.info("Temporarily enabling public access for deployment...")
        update_public_access(resources, enabled=True)
        configure_ui_registry(resources)
        wait_for_acr(resources)
        return 0
    except (KeyError, RuntimeError, json.JSONDecodeError) as error:
        LOGGER.error("Public access could not be enabled safely: %s", error)
        if "resources" in locals():
            try:
                update_public_access(resources, enabled=False, continue_on_error=True)
            except RuntimeError as rollback_error:
                LOGGER.error("Public access rollback failed: %s", rollback_error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())