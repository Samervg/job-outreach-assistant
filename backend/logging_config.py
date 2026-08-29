import logging

from backend.config import LOG_LEVEL


def configure_logging() -> None:
    """Configure concise backend logs without request or secret payloads."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

