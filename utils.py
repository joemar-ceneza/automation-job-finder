"""
utils.py
Generic reusable helpers with no project-specific knowledge.
"""
import logging
import time
from typing import Any, Callable


def retry(func: Callable[[], Any], retries: int = 3, delay: float = 2.0,
          backoff: float = 2.0,
          give_up_on: tuple[type[Exception], ...] = ()) -> Any:
    """
    Calls func() up to `retries` times, sleeping between attempts with
    exponential backoff (delay, delay*backoff, delay*backoff^2, ...).
    Returns func()'s result on success; raises after the final failure.
    Exceptions listed in give_up_on propagate immediately without retrying
    (for failures that retrying can't fix, e.g. anti-bot blocks).
    """
    wait_seconds = delay
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except give_up_on:
            raise
        except Exception as e:
            last_error = e
            logging.warning("Attempt %d of %d failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(wait_seconds)
                wait_seconds *= backoff
    raise Exception(f"All {retries} retry attempts failed: {last_error}")
