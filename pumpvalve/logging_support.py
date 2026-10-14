import logging
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from .storage import data_dir

logger = logging.getLogger('pumpvalve')


def configure():
    if not logger.handlers:
        handler = RotatingFileHandler(data_dir() / 'application.log', maxBytes=2_000_000,
                                      backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def log_transaction(device, action, **details):
    logger.info('%s %s %r', device, action, details)


@contextmanager
def log_call(device, action, **details):
    result = {}
    try:
        yield result
    except Exception as exc:
        log_transaction(device, action, **details, error=str(exc), success=False)
        raise
    else:
        log_transaction(device, action, **details, **result)


def run_with_timeout(action, name, timeout):
    # Run only on the bus owner. Never launch concurrent SDK cleanup calls.
    # The application bounds waiting and quarantines a stalled bus.
    try:
        action()
    except Exception as exc:
        return f'{name}: {exc}'
    return None
