"""
JWT认证模块
密码hash + JWT生成验证 + 登录注册 + 用户依赖注入
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import bcrypt
from starlette.concurrency import run_in_threadpool

try:
    from models import (
        create_user, get_user_by_username, get_user_by_email,
        get_user_by_id, update_last_login, clean_expired_tokens
    )
except ImportError:  # pragma: no cover - package execution fallback
    from .models import (
        create_user, get_user_by_username, get_user_by_email,
        get_user_by_id, update_last_login, clean_expired_tokens
    )

# ─── 配置 ─────────────────────────────────────────────────

SECRET_KEY = os.getenv("STOCK_ANALYZER_SECRET_KEY", "stock-analyzer-dev-secret-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
BCRYPT_MAX_PASSWORD_BYTES = 72

if not hasattr(bcrypt, "__about__"):
    class _BcryptAbout:
        __version__ = getattr(bcrypt, "__version__", "unknown")

    bcrypt.__about__ = _BcryptAbout()

# ─── 密码hash ─────────────────────────────────────────────

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """对密码进行bcrypt hash"""
    password_bytes = password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def password_byte_length(password: str) -> int:
    """Return password length in UTF-8 bytes, matching bcrypt's 72-byte limit."""
    return len((password or "").encode("utf-8"))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    if password_byte_length(plain_password) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


# ─── JWT Token ────────────────────────────────────────────

def create_access_token(user_id: int, username: str) -> dict:
    """生成JWT token，返回token信息"""
    expires_delta = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    expire = datetime.now(timezone.utc) + expires_delta
    
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expire.isoformat()
    }


def decode_token(token: str) -> Optional[dict]:
    """解码JWT token，返回payload或None"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


# ─── 注册 ─────────────────────────────────────────────────

def register_user(username: str, email: str, password: str) -> dict:
    """
    用户注册
    返回用户信息（不含密码）
    """
    # 参数验证
    if not username or len(username) < 3:
        raise ValueError("用户名至少3个字符")
    if not email or "@" not in email:
        raise ValueError("邮箱格式不正确")
    if not password or len(password) < 6:
        raise ValueError("密码至少6个字符")
    if password_byte_length(password) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError("密码不能超过72字节，请缩短后重试")
    
    # 检查用户名是否已存在
    if get_user_by_username(username):
        raise ValueError("用户名已存在")
    
    # 检查邮箱是否已注册
    if get_user_by_email(email):
        raise ValueError("邮箱已被注册")
    
    # 创建用户
    password_hash = hash_password(password)
    user = create_user(username, email, password_hash)
    return user


# ─── 登录 ─────────────────────────────────────────────────

def login_user(username: str, password: str) -> dict:
    """
    用户登录
    返回token信息
    """
    # 查找用户
    user = get_user_by_username(username)
    if not user:
        raise ValueError("用户名或密码错误")
    
    # 验证密码
    if not verify_password(password, user["password_hash"]):
        raise ValueError("用户名或密码错误")
    
    # 生成token
    token_data = create_access_token(user["id"], user["username"])
    
    # 更新最后登录时间
    update_last_login(user["id"])
    
    # 清理过期token
    clean_expired_tokens()
    
    return {
        **token_data,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
            "credits": user["credits"]
        }
    }


# ─── 依赖注入 ─────────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> dict:
    """
    FastAPI依赖注入：从Authorization header获取当前用户
    需要登录的接口使用此依赖
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "未登录", "code": 401}
        )
    
    token = credentials.credentials
    payload = decode_token(token)
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "Token无效或已过期", "code": 401}
        )
    
    user_id = int(payload.get("sub", 0))
    user = await run_in_threadpool(get_user_by_id, user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "用户不存在", "code": 401}
        )
    
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "credits": user["credits"]
    }
