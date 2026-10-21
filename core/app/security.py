import time

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

_hasher = PasswordHasher()
COOKIE = "jarvis_session"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except VerificationError:
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="session")


def make_session(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session(token: str) -> int | None:
    try:
        return _serializer().loads(token, max_age=settings.session_hours * 3600)["uid"]
    except (BadSignature, KeyError):
        return None


class LoginLimiter:
    """In-memory brute-force guard: 5 failures lock a key for 15 minutes."""

    MAX_FAILS = 5
    LOCK_SECONDS = 15 * 60

    def __init__(self) -> None:
        self._fails: dict[str, tuple[int, float]] = {}

    def locked(self, key: str) -> bool:
        count, ts = self._fails.get(key, (0, 0.0))
        if count >= self.MAX_FAILS:
            if time.monotonic() - ts < self.LOCK_SECONDS:
                return True
            self._fails.pop(key, None)
        return False

    def fail(self, key: str) -> None:
        count, _ = self._fails.get(key, (0, 0.0))
        self._fails[key] = (count + 1, time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


limiter = LoginLimiter()
