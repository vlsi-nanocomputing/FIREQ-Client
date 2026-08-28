"""Entry point for the FIREQ client.

This script starts the client and asks the user to which IP:PORT it should connect.
"""

import logging
import sys

from FIREQ_CLIENT.client import Client


def setup_logging(level: int) -> logging.Logger:
    """Configure logging for the server."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger(__name__)


def main() -> None:
    """Prompt for IP and PORT of the server and start the client."""
    logger = setup_logging(level=logging.INFO)
    logger.info("### FIREQ Client startup ###\n")

    # set logging level
    log_level = input("Input logging level: 'debug', 'info' (press enter for 'info')\n")
    if log_level == "debug":
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        pass

    # Get server configuration
    server_ip = input('# Insert server IP (press Enter for "0.0.0.0")\n').strip()
    if not server_ip:
        server_ip = "0.0.0.0"

    server_port = input('# Insert server port (press Enter for "5000")\n').strip()
    if not server_port:
        server_port = 5000
    else:
        try:
            server_port = int(server_port)
        except ValueError:
            logger.error(f"Invalid port number: {server_port}")
            sys.exit(-1)

    auth_token = input('# Insert auth token (press Enter for "fireq")\n').strip()
    if not auth_token:
        auth_token = "fireq"

    # run the server
    client = Client(server_ip, server_port)
    client.run()


if __name__ == "__main__":
    main()
