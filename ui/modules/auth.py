#modules/auth.py
import os  
import json  
import base64  
from typing import Optional, List, Callable  
from fastapi import Request, WebSocket, HTTPException  
from modules.config import TEST_GROUP, ADMIN_GROUP_ID, USER_GROUP_ID

  
  
ADMIN_USERS = {  
    x.strip().lower()  
    for x in os.getenv("ADMIN_USERS", "").split(",")  
    if x.strip()  
}  
USER_USERS = {  
    x.strip().lower()  
    for x in os.getenv("USER_USERS", "").split(",")  
    if x.strip()  
}  
  
  
class UserContext:  
    def __init__(  
        self,  
        user_id: str = "",  
        user_name: str = "",  
        user_email: str = "",  
        group: str = "",  
        claims: Optional[List[dict]] = None,  
        auth_source: str = "",  
    ):  
        self.user_id = user_id  
        self.user_name = user_name  
        self.user_email = user_email  
        self.group = group  
        self.claims = claims or []  
        self.auth_source = auth_source  
  
    @property  
    def is_admin(self) -> bool:  
        return self.group == "admin"  
  
    @property  
    def is_user(self) -> bool:  
        return self.group == "user"  
  
    @property  
    def is_authorized(self) -> bool:  
        return self.group in {"admin", "user"}  
  
    def to_dict(self) -> dict:  
        return {  
            "user_id": self.user_id,  
            "user_name": self.user_name,  
            "user_email": self.user_email,  
            "group": self.group,  
            "claims": self.claims,  
            "auth_source": self.auth_source,  
            "is_admin": self.is_admin,  
            "is_user": self.is_user,  
            "is_authorized": self.is_authorized,  
        }  
  
  
def _decode_client_principal(header_value: str) -> dict:  
    decoded = base64.b64decode(header_value)  
    return json.loads(decoded.decode("utf-8"))  
  
  
def _extract_user_identifiers(principal: dict) -> dict:  
    claims = principal.get("claims", [])  
  
    user_id = principal.get("userId", "") or principal.get("user_id", "")  
    user_name = principal.get("userDetails", "") or ""  
  
    emails = []  
    preferred_usernames = []  
    names = []  
  
    for c in claims:  
        typ = c.get("typ", "") or ""  
        val = c.get("val", "") or ""  
        if not val:  
            continue  
  
        if typ == "preferred_username" or typ.endswith("/claims/preferredusername"):  
            preferred_usernames.append(val)  
        elif typ == "email" or typ.endswith("/claims/emailaddress"):  
            emails.append(val)  
        elif typ == "name" or typ.endswith("/claims/name"):  
            names.append(val)  
  
    user_email = ""  
    if preferred_usernames:  
        user_email = preferred_usernames[0]  
    elif emails:  
        user_email = emails[0]  
    elif user_name:  
        user_email = user_name  
  
    display_name = names[0] if names else (user_name or user_email or "")  
  
    return {  
        "user_id": user_id,  
        "user_name": display_name,  
        "user_email": (user_email or "").lower(),  
    }  
  
  
def resolve_user_context_from_principal(principal: Optional[dict]) -> UserContext:  
    principal = principal or {}  
    print(f"TEST_GROUP={TEST_GROUP}, ADMIN_GROUP_ID={ADMIN_GROUP_ID}, USER_GROUP_ID={USER_GROUP_ID}")  
  
    claims = principal.get("claims", []) if principal else []  
  
    user_info = _extract_user_identifiers(principal) if principal else {  
        "user_id": "",  
        "user_name": "",  
        "user_email": "",  
    }  
  
    user_id = user_info["user_id"]  
    user_name = user_info["user_name"]  
    user_email = user_info["user_email"]  
  
    # 1. 強制指定  
    if TEST_GROUP in {"admin", "user"}:  
        print(f"TEST_GROUP is set to '{TEST_GROUP}', forcing user group")  
        return UserContext(  
            user_id=user_id,  
            user_name=user_name,  
            user_email=user_email,  
            group=TEST_GROUP,  
            claims=claims,  
            auth_source="env:TEST_GROUP",  
        )  
  
    # 2. 個別ユーザー指定  
    if user_email:  
        if user_email in ADMIN_USERS:  
            return UserContext(  
                user_id=user_id,  
                user_name=user_name,  
                user_email=user_email,  
                group="admin",  
                claims=claims,  
                auth_source="env:ADMIN_USERS",  
            )  
  
        if user_email in USER_USERS:  
            return UserContext(  
                user_id=user_id,  
                user_name=user_name,  
                user_email=user_email,  
                group="user",  
                claims=claims,  
                auth_source="env:USER_USERS",  
            )  
  
    # 3. AAD claims  
    group_ids = []  
    role_values = []  
  
    for c in claims:  
        typ = c.get("typ", "") or ""  
        val = c.get("val", "") or ""  
        if not val:  
            continue  
  
        if typ == "groups" or typ.endswith("/claims/groups"):  
            group_ids.append(val)  
        elif typ == "roles" or typ.endswith("/claims/role"):  
            role_values.append(val.lower())  
  
    print(  
        "[auth] resolved principal:",  
        {  
            "user_id": user_id,  
            "user_name": user_name,  
            "user_email": user_email,  
            "group_ids": group_ids,  
            "role_values": role_values,  
        }  
    )  
  
    if ADMIN_GROUP_ID and ADMIN_GROUP_ID in group_ids:  
        print(f"[auth] matched admin group: {ADMIN_GROUP_ID}")  
        return UserContext(  
            user_id=user_id,  
            user_name=user_name,  
            user_email=user_email,  
            group="admin",  
            claims=claims,  
            auth_source="aad:group",  
        )  
  
    if USER_GROUP_ID and USER_GROUP_ID in group_ids:  
        print(f"[auth] matched user group: {USER_GROUP_ID}")  
        return UserContext(  
            user_id=user_id,  
            user_name=user_name,  
            user_email=user_email,  
            group="user",  
            claims=claims,  
            auth_source="aad:group",  
        )  
  
    if "admin" in role_values:  
        print("[auth] matched admin role")  
        return UserContext(  
            user_id=user_id,  
            user_name=user_name,  
            user_email=user_email,  
            group="admin",  
            claims=claims,  
            auth_source="aad:role",  
        )  
  
    if "user" in role_values:  
        print("[auth] matched user role")  
        return UserContext(  
            user_id=user_id,  
            user_name=user_name,  
            user_email=user_email,  
            group="user",  
            claims=claims,  
            auth_source="aad:role",  
        )  
  
    print("[auth] no matching group/role found")  
    return UserContext(  
        user_id=user_id,  
        user_name=user_name,  
        user_email=user_email,  
        group="",  
        claims=claims,  
        auth_source="none",  
    )  
  
  
def get_user_context(request: Request) -> UserContext:  
    header = request.headers.get("x-ms-client-principal")  
    if not header:  
        print("No x-ms-client-principal header found in request")
        return resolve_user_context_from_principal(None)  
  
    try:  
        principal = _decode_client_principal(header)  
        return resolve_user_context_from_principal(principal)  
    except Exception:  
        return resolve_user_context_from_principal(None)  
  
  
def get_user_context_from_websocket(websocket: WebSocket) -> UserContext:  
    header = websocket.headers.get("x-ms-client-principal")  
    if not header:  
        return resolve_user_context_from_principal(None)  
  
    try:  
        principal = _decode_client_principal(header)  
        return resolve_user_context_from_principal(principal)  
    except Exception:  
        return resolve_user_context_from_principal(None)  
  
  
def require_admin(request: Request) -> UserContext:  
    user = get_user_context(request)  
    if not user.is_admin:  
        raise HTTPException(status_code=403, detail="admin only")  
    return user  
  
  
def require_user_or_admin(request: Request) -> UserContext:  
    user = get_user_context(request)  
    if not user.is_authorized:  
        raise HTTPException(status_code=403, detail="forbidden")  
    return user  
  
  
def resolve_prompt_and_model_for_request(  
    request: Request,  
    prompt_set_name: Optional[str],  
    model_name: Optional[str],  
    validate_prompt_fn: Callable[[str], None],  
    validate_model_fn: Callable[[str], None],  
    get_default_model_fn: Callable[[], str],  
):  
    user = get_user_context(request)  
  
    if user.is_admin:  
        final_prompt = (prompt_set_name or "default").strip() or "default"  
        final_model = (model_name or get_default_model_fn()).strip() or get_default_model_fn()  
  
        validate_prompt_fn(final_prompt)  
        validate_model_fn(final_model)  
        return final_prompt, final_model  
  
    if user.is_user:  
        return "default", get_default_model_fn()  
  
    raise HTTPException(status_code=403, detail="forbidden")  