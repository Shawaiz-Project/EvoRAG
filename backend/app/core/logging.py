import logging
import time
from logging.config import dictConfig


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "application": {
                    "format": "%(asctime)sZ %(levelname)s %(name)s %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S",
                    "()": UTCFormatter,
                }
            },
            "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "application"}},
            "loggers": {"app": {"handlers": ["console"], "level": level, "propagate": False}},
        }
    )


class UTCFormatter(logging.Formatter):
    converter = time.gmtime
