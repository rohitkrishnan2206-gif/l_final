import time
from app.security import (SecretBox, LoginLimiter, hash_password, verify_password, create_token, decode_token,
                          check_password_policy, role_allows)
from cryptography.fernet import Fernet
from app.redact import redact


def test_password_hash_roundtrip_and_salt():
    h1, h2 = hash_password("Secret12345", 1000), hash_password("Secret12345", 1000)
    assert h1 != h2 and verify_password("Secret12345", h1) and not verify_password("wrong", h1)
    assert not verify_password("x", "garbage")


def test_password_policy():
    assert check_password_policy("short1A")
    assert check_password_policy("alllowercase123")
    assert check_password_policy("GoodPassw0rd") is None


def test_jwt_tamper_and_expiry():
    t = create_token("s" * 32, 1, "admin", 5)
    assert decode_token("s" * 32, t)["sub"] == "1"
    assert decode_token("other" * 8, t) is None
    assert decode_token("s" * 32, create_token("s" * 32, 1, "admin", -1)) is None


def test_secretbox_encrypts_and_rejects_wrong_key():
    box = SecretBox(Fernet.generate_key().decode())
    tok = box.encrypt({"k": "s3cr3t-value"})
    assert "s3cr3t" not in tok and box.decrypt(tok) == {"k": "s3cr3t-value"}
    assert SecretBox(Fernet.generate_key().decode()).decrypt(tok) == {}


def test_limiter_locks_after_five():
    l = LoginLimiter(5, 300)
    for _ in range(5):
        l.fail("a")
    assert l.locked("a") and not l.locked("b")
    l.reset("a")
    assert not l.locked("a")


def test_roles():
    assert role_allows("admin", "viewer") and role_allows("analyst", "analyst") and not role_allows("viewer", "analyst")


def test_redaction():
    out = redact("password=hunter2 key AKIAABCDEFGHIJKLMNOP mail a@b.com card 4111 1111 1111 1111 order 1234567890123")
    assert "hunter2" not in out and "AKIA" not in out and "a@b.com" not in out
    assert "4111" not in out and "1234567890123" in out  # non-Luhn number kept


def test_secretbox_accepts_long_passphrase_rejects_short():
    import pytest
    box = SecretBox("a-long-random-passphrase-with-at-least-32-chars-123")
    assert box.decrypt(box.encrypt({"a": 1})) == {"a": 1}
    with pytest.raises(ValueError, match="at least 32"):
        SecretBox("short")


def test_admin_password_reset_switch(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app

    def login(c, pw):
        return c.post("/api/auth/login", json={"username": "admin", "password": pw}).status_code

    d = tmp_path / "data"
    c = TestClient(create_app(Settings(data_dir=d, disable_scheduler=True, admin_password="OldPassword123")))
    assert login(c, "OldPassword123") == 200
    # env var alone does nothing once the admin exists
    c = TestClient(create_app(Settings(data_dir=d, disable_scheduler=True, admin_password="NewPassword123")))
    assert login(c, "NewPassword123") == 401 and login(c, "OldPassword123") == 200
    # with the recovery flag the password is reset, and the account is usable straight away
    app = create_app(Settings(data_dir=d, disable_scheduler=True, admin_password="NewPassword123", reset_admin=True))
    app.state.ctx.db.execute("UPDATE users SET disabled=1 WHERE username='admin'")
    c = TestClient(create_app(Settings(data_dir=d, disable_scheduler=True, admin_password="NewPassword123", reset_admin=True)))
    assert login(c, "NewPassword123") == 200 and login(c, "OldPassword123") == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "NewPassword123"}).json().get("must_change") in (False, 0, None)
    # weak password in the flag is refused, old password stays
    c = TestClient(create_app(Settings(data_dir=d, disable_scheduler=True, admin_password="weak", reset_admin=True)))
    assert login(c, "weak") == 401 and login(c, "NewPassword123") == 200
