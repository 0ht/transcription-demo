# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import logging

from network_access import load_network_resources, update_public_access


logging.basicConfig(level=logging.INFO, format=">>> %(message)s")
LOGGER = logging.getLogger(__name__)


def main() -> int:
    try:
        resources = load_network_resources()
        LOGGER.info("Disabling public access after deployment...")
        update_public_access(resources, enabled=False, continue_on_error=True)
        LOGGER.info("Public access disabled.")
        return 0
    except (KeyError, RuntimeError, json.JSONDecodeError) as error:
        LOGGER.error("Public access could not be fully disabled: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())