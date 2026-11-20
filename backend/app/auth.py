import argparse
import asyncio
import getpass
from hashlib import scrypt
from hmac import compare_digest
from secrets import token_bytes

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.identity import Role, User, UserRole
from app.db.session import session_factory


def password_hash(password: str, salt: bytes) -> str:
    return scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def encode_password(password: str) -> str:
    salt = token_bytes(16)
    return f"{salt.hex()}:{password_hash(password, salt)}"


def verify_password(password: str, encoded: str | None) -> bool:
    if encoded is None:
        password_hash(password, bytes(16))
        return False
    salt, digest = encoded.split(":")
    return compare_digest(password_hash(password, bytes.fromhex(salt)), digest)


async def bootstrap_admin(email: str, password: str) -> None:
    engine = create_async_engine(Settings().database_url)
    try:
        async with session_factory(engine).begin() as session:
            role = await session.scalar(sa.select(Role).where(Role.key == "admin"))
            if role is None:
                raise ValueError("Seed the application roles before creating an administrator")
            if await session.scalar(sa.select(User.id).where(sa.func.lower(User.email) == email)):
                raise ValueError("A user with that email already exists")
            user = User(email=email, display_name=email, password_hash=encode_password(password))
            session.add(user)
            await session.flush()
            session.add(UserRole(user_id=user.id, role_id=role.id))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an EvoRAG administrator.")
    parser.add_argument("--email", required=True)
    email = parser.parse_args().email.strip().casefold()
    if "@" not in email or len(email) > 320:
        parser.error("Enter a valid email address")
    password = getpass.getpass("Password (minimum 12 characters): ")
    if (
        len(password) < 12
        or len(password) > 256
        or password != getpass.getpass("Confirm password: ")
    ):
        parser.error("Passwords must match and be 12 to 256 characters")
    asyncio.run(bootstrap_admin(email, password))
