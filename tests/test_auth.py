import pytest
import sys
from types import ModuleType
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request

config_stub = ModuleType("modules.config")
config_stub.TEST_GROUP = ""
config_stub.ADMIN_GROUP_ID = ""
config_stub.USER_GROUP_ID = ""
with patch.dict(sys.modules, {"modules.config": config_stub}):
    from modules import auth


@pytest.fixture(autouse=True)
def explicit_authorization(monkeypatch):
    monkeypatch.setattr(auth, "TEST_GROUP", "")
    monkeypatch.setattr(auth, "ADMIN_USERS", set())
    monkeypatch.setattr(auth, "USER_USERS", set())
    monkeypatch.setattr(auth, "ADMIN_USER_IDS", {"allowed-object-id"})


@pytest.mark.unit
@pytest.mark.parametrize("claim_type", [
    "oid", "http://schemas.microsoft.com/identity/claims/objectidentifier",
])
def test_explicit_admin_object_id_from_easyauth_claims(claim_type):
    user = auth.resolve_user_context_from_principal({
        "claims": [{"typ": claim_type, "val": "allowed-object-id"}],
    })
    assert user.user_id == "allowed-object-id"
    assert user.is_admin


@pytest.mark.unit
@pytest.mark.parametrize("principal", [None, {}, {
    "claims": [{"typ": "oid", "val": "another-object-id"}],
}])
def test_unknown_or_missing_identity_is_not_granted_access(principal):
    assert not auth.resolve_user_context_from_principal(principal).is_authorized


@pytest.mark.unit
def test_history_authorization_remains_fail_closed():
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as error:
        auth.require_user_or_admin(request)
    assert error.value.status_code == 403
