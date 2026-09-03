import random
import time


def is_rate_limit_error(error):
    text = str(error).lower()
    return "429" in text or "quota exceeded" in text or "rate limit" in text


class SheetsAccessStopped(RuntimeError):
    pass


class SheetsRunContext:
    def __init__(self, max_retries=3, backoff_base_seconds=3, sleep=time.sleep):
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        self.sleep = sleep
        self.read_count = 0
        self.write_count = 0
        self.cache = {}

    def reset(self):
        self.read_count = 0
        self.write_count = 0
        self.cache.clear()

    def request(self, kind, operation, action):
        for attempt in range(self.max_retries + 1):
            if kind == "read":
                self.read_count += 1
            else:
                self.write_count += 1
            try:
                return action()
            except Exception as error:
                if not is_rate_limit_error(error):
                    raise
                print(f"SHEETS_429: api={operation} retry={attempt}/{self.max_retries} error=quota_exceeded")
                if attempt >= self.max_retries:
                    print(f"SHEETS_ABORT: api={operation} retries_exhausted={self.max_retries}")
                    raise SheetsAccessStopped(operation) from error
                delay = self.backoff_base_seconds * (2 ** attempt)
                delay += random.uniform(0, min(1.0, self.backoff_base_seconds))
                self.sleep(delay)
