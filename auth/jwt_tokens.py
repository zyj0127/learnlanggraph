# -*- coding: utf-8 -*-
"""JWT 签发与校验（PyJWT 延迟导入）。

当前实现：HS256 对称签名，密钥走 Settings.jwt_secret。

企业 SSO / OIDC 扩展点（替换位置已标注）：
- 接企业 SSO 时，将算法切到 RS256（Settings.jwt_algorithm="RS256"），
  把 decode_token 中的密钥替换为从 IdP JWKS endpoint 拉取的公钥集合
  （推荐 jwt.PyJWKClient(settings.oidc_jwks_url)，带缓存与 kid 轮换），
  并补充 iss / aud 校验。encode_token（开发签发端点）届时可整体下线。
- payload → Identity 的字段映射（sub/role/name/dept）保持不变，
  下游 RBAC 与审计口径无感知。
"""
import time
from typing import Optional

from auth.models import Identity, Role


def _jwt():
    """延迟导入 PyJWT；未安装时给出明确指引（纯逻辑测试不依赖 PyJWT）。"""
    try:
        import jwt

        return jwt
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 PyJWT 依赖：请先 pip install PyJWT==2.10.1（或 pip install -e .）"
        ) from exc


def encode_token(identity: Identity, *, secret: str, algorithm: str = "HS256",
                 expire_minutes: int = 120) -> str:
    """签发 JWT（仅开发模式 /auth/token 使用；生产由企业 SSO 签发）。"""
    jwt = _jwt()
    now = int(time.time())
    payload = {
        "sub": identity.uid,
        "role": identity.role.value,
        "name": identity.name,
        "dept": identity.department,
        "iat": now,
        "exp": now + int(expire_minutes) * 60,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str, *, secret: str, algorithm: str = "HS256") -> Optional[Identity]:
    """校验 JWT 并映射为 Identity；签名/过期/结构任何异常一律返回 None（按匿名处理）。

    RS256/OIDC 替换点：生产接 SSO 时，secret 改为 JWKS 公钥（见模块头注释），
    并增加 iss/audience 校验；返回 Identity 的字段映射不变。
    """
    jwt = _jwt()
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        role = Role(str(payload.get("role", Role.ANONYMOUS.value)))
        return Identity(
            uid=str(payload.get("sub", "")),
            name=str(payload.get("name", "")),
            role=role,
            department=str(payload.get("dept", "")),
        )
    except Exception:
        return None
