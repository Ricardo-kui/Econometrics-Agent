import uuid
from typing import Optional
from datetime import datetime, timedelta

import peewee as pw
from loguru import logger
from pydantic import BaseModel

from chatpilot.apps.auth_utils import verify_password
from chatpilot.apps.db import DB
from chatpilot.apps.web.models.users import UserModel, Users


####################
# DB MODEL
####################


class Auth(pw.Model):
    id = pw.CharField(unique=True)
    email = pw.CharField()
    password = pw.CharField()
    active = pw.BooleanField()
    # Email verification fields (optional: could be managed in User table only)
    email_verified = pw.BooleanField(default=False)
    verification_token = pw.CharField(null=True)
    verification_expires = pw.DateTimeField(null=True)

    class Meta:
        database = DB


class AuthModel(BaseModel):
    id: str
    email: str
    password: str
    active: bool = True
    # Email verification fields
    email_verified: bool = False
    verification_token: Optional[str] = None
    verification_expires: Optional[int] = None  # timestamp in epoch


####################
# Forms
####################


class Token(BaseModel):
    token: str
    token_type: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    profile_image_url: str


class SigninResponse(Token, UserResponse):
    pass


class SigninForm(BaseModel):
    email: str
    password: str


class ProfileImageUrlForm(BaseModel):
    profile_image_url: str


class UpdateProfileForm(BaseModel):
    profile_image_url: str
    name: str


class UpdatePasswordForm(BaseModel):
    password: str
    new_password: str


class SignupForm(BaseModel):
    name: str
    email: str
    password: str


class SignupResponse(BaseModel):
    message: str
    email: str
    requires_verification: bool = True


class AuthsTable:
    def __init__(self, db):
        self.db = db
        self.db.create_tables([Auth])

    def insert_new_auth(
            self, email: str, password: str, name: str, role: str = "user"
    ) -> Optional[UserModel]:
        logger.debug(f"insert_new_auth, role: {role}, email: {email}, name: {name}")
        id = str(uuid.uuid4())
        auth = AuthModel(
            **{
                "id": id, 
                "email": email, 
                "password": password, 
                "active": True,
                "email_verified": False,
                "verification_token": None,
                "verification_expires": None,
            }
        )
        result = Auth.create(**auth.model_dump())

        user = Users.insert_new_user(id, name, email, role)

        if result and user:
            return user
        else:
            return None

    def authenticate_user(self, email: str, password: str) -> Optional[UserModel]:
        logger.debug(f"authenticate_user, email: {email}")
        try:
            auth = Auth.get(Auth.email == email, Auth.active == True)
            if auth:
                if verify_password(password, auth.password):
                    user = Users.get_user_by_id(auth.id)
                    return user
                else:
                    return None
            else:
                return None
        except:
            return None

    def update_user_password_by_id(self, id: str, new_password: str) -> bool:
        try:
            query = Auth.update(password=new_password).where(Auth.id == id)
            result = query.execute()

            return True if result == 1 else False
        except:
            return False

    def update_email_by_id(self, id: str, email: str) -> bool:
        try:
            query = Auth.update(email=email).where(Auth.id == id)
            result = query.execute()

            return True if result == 1 else False
        except:
            return False

    def delete_auth_by_id(self, id: str) -> bool:
        try:
            # Delete User
            result = Users.delete_user_by_id(id)

            if result:
                # Delete Auth
                query = Auth.delete().where(Auth.id == id)
                query.execute()  # Remove the rows, return number of rows removed.

                return True
            else:
                return False
        except:
            return False

    def set_auth_verification_token(self, id: str, token: str, expires_hours: int = 24) -> bool:
        """Set email verification token for auth record"""
        try:
            expires_time = datetime.now() + timedelta(hours=expires_hours)
            query = Auth.update(
                verification_token=token,
                verification_expires=expires_time
            ).where(Auth.id == id)
            result = query.execute()
            return True if result == 1 else False
        except Exception as e:
            logger.error(f"Error setting auth verification token: {e}")
            return False

    def verify_auth_email(self, token: str) -> bool:
        """Verify auth email using verification token"""
        try:
            auth = Auth.get(
                Auth.verification_token == token,
                Auth.verification_expires > datetime.now()
            )
            if auth:
                # Update auth as verified and clear verification fields
                query = Auth.update(
                    email_verified=True,
                    verification_token=None,
                    verification_expires=None
                ).where(Auth.id == auth.id)
                query.execute()
                return True
        except Exception as e:
            logger.error(f"Error verifying auth email: {e}")
            return False


Auths = AuthsTable(DB)
