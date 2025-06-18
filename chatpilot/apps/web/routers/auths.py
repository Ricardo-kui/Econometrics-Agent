import re

from fastapi import APIRouter, status
from fastapi import Depends, HTTPException
from fastapi import Request
from loguru import logger
from pydantic import BaseModel

from chatpilot.apps.auth_utils import (
    get_password_hash,
    get_current_user,
    get_admin_user,
    create_token,
)
from chatpilot.apps.misc import parse_duration, validate_email_format, validate_password_format
from chatpilot.apps.email_utils import (
    send_verification_email, 
    generate_verification_token,
    send_welcome_email
)
from chatpilot.apps.web.models.auths import (
    SigninForm,
    SignupForm,
    UpdateProfileForm,
    UpdatePasswordForm,
    UserResponse,
    SigninResponse,
    SignupResponse,
    Auths,
)
from chatpilot.apps.web.models.users import Users
from chatpilot.constants import ERROR_MESSAGES
from chatpilot.config import EMAIL_VERIFICATION_ENABLED, WEBUI_BASE_URL

router = APIRouter()


############################
# GetSessionUser
############################


@router.get("/", response_model=UserResponse)
async def get_session_user(user=Depends(get_current_user)):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "profile_image_url": user.profile_image_url,
    }


############################
# Update Profile
############################


@router.post("/update/profile", response_model=UserResponse)
async def update_profile(
        form_data: UpdateProfileForm, session_user=Depends(get_current_user)
):
    if session_user:
        user = Users.update_user_by_id(
            session_user.id,
            {"profile_image_url": form_data.profile_image_url, "name": form_data.name},
        )
        if user:
            return user
        else:
            raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT())
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# Update Password
############################


@router.post("/update/password", response_model=bool)
async def update_password(
        form_data: UpdatePasswordForm, session_user=Depends(get_current_user)
):
    if session_user:
        user = Auths.authenticate_user(session_user.email, form_data.password)

        if user:
            hashed = get_password_hash(form_data.new_password)
            return Auths.update_user_password_by_id(user.id, hashed)
        else:
            raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_PASSWORD)
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# SignIn
############################


@router.post("/signin", response_model=SigninResponse)
async def signin(request: Request, form_data: SigninForm):
    user = Auths.authenticate_user(form_data.email.lower(), form_data.password)
    if user:
        # Check if email verification is required and enabled
        if EMAIL_VERIFICATION_ENABLED and not user.email_verified:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Please verify your email before signing in. Check your inbox for the verification link."
            )
        
        token = create_token(
            data={"id": user.id},
            expires_delta=parse_duration(request.app.state.JWT_EXPIRES_IN),
        )
        if user.role == "admin":
            logger.debug(f"Admin user signed in: {user.email}, token: {token}")

        return {
            "token": token,
            "token_type": "Bearer",
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "profile_image_url": user.profile_image_url,
        }
    else:
        raise HTTPException(400, detail=ERROR_MESSAGES.INVALID_CRED)


############################
# SignUp
############################


@router.post("/signup")
async def signup(request: Request, form_data: SignupForm):
    if not request.app.state.ENABLE_SIGNUP:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
        )

    if not validate_email_format(form_data.email.lower()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_EMAIL_FORMAT
        )

    if not validate_password_format(form_data.password):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.INVALID_PASSWORD_FORMAT
        )

    if Users.get_user_by_email(form_data.email.lower()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.EMAIL_TAKEN)

    try:
        role = (
            "admin"
            if Users.get_num_users() == 0
            else request.app.state.DEFAULT_USER_ROLE
        )
        hashed = get_password_hash(form_data.password)
        user = Auths.insert_new_auth(
            form_data.email.lower(), hashed, form_data.name, role
        )

        if user:
            # If email verification is enabled, send verification email
            if EMAIL_VERIFICATION_ENABLED:
                # Generate verification token
                verification_token = generate_verification_token()
                
                # Save token to both User and Auth tables
                Users.set_user_verification_token(user.id, verification_token)
                Auths.set_auth_verification_token(user.id, verification_token)
                
                # Send verification email
                if send_verification_email(user.email, user.name, verification_token):
                    return SignupResponse(
                        message="注册成功！请检查你的邮箱并点击验证链接完成注册。",
                        email=user.email,
                        requires_verification=True
                    )
                else:
                    logger.error(f"Failed to send verification email to {user.email}")
                    # If email sending fails, still allow registration but log the error
                    return SignupResponse(
                        message="注册成功！但验证邮件发送失败，请联系技术支持。",
                        email=user.email,
                        requires_verification=True
                    )
            else:
                # If email verification is disabled, directly login user (backward compatibility)
                token = create_token(
                    data={"id": user.id},
                    expires_delta=parse_duration(request.app.state.JWT_EXPIRES_IN),
                )
                
                return {
                    "token": token,
                    "token_type": "Bearer",
                    "id": user.id,
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    "profile_image_url": user.profile_image_url,
                }
        else:
            raise HTTPException(500, detail=ERROR_MESSAGES.CREATE_USER_ERROR)
    except Exception as err:
        raise HTTPException(500, detail=ERROR_MESSAGES.DEFAULT(err))


############################
# ToggleSignUp
############################


@router.get("/signup/enabled", response_model=bool)
async def get_sign_up_status(request: Request, user=Depends(get_admin_user)):
    return request.app.state.ENABLE_SIGNUP


@router.get("/signup/enabled/toggle", response_model=bool)
async def toggle_sign_up(request: Request, user=Depends(get_admin_user)):
    request.app.state.ENABLE_SIGNUP = not request.app.state.ENABLE_SIGNUP
    return request.app.state.ENABLE_SIGNUP


############################
# Default User Role
############################


@router.get("/signup/user/role")
async def get_default_user_role(request: Request, user=Depends(get_admin_user)):
    return request.app.state.DEFAULT_USER_ROLE


class UpdateRoleForm(BaseModel):
    role: str


@router.post("/signup/user/role")
async def update_default_user_role(
        request: Request, form_data: UpdateRoleForm, user=Depends(get_admin_user)
):
    if form_data.role in ["pending", "user", "admin"]:
        request.app.state.DEFAULT_USER_ROLE = form_data.role
    return request.app.state.DEFAULT_USER_ROLE


############################
# JWT Expiration
############################


@router.get("/token/expires")
async def get_token_expires_duration(request: Request, user=Depends(get_admin_user)):
    return request.app.state.JWT_EXPIRES_IN


class UpdateJWTExpiresDurationForm(BaseModel):
    duration: str


@router.post("/token/expires/update")
async def update_token_expires_duration(
        request: Request,
        form_data: UpdateJWTExpiresDurationForm,
        user=Depends(get_admin_user),
):
    pattern = r"^(-1|0|(-?\d+(\.\d+)?)(ms|s|m|h|d|w))$"

    # Check if the input string matches the pattern
    if re.match(pattern, form_data.duration):
        request.app.state.JWT_EXPIRES_IN = form_data.duration
        return request.app.state.JWT_EXPIRES_IN
    else:
        return request.app.state.JWT_EXPIRES_IN


############################
# Email Verification
############################


class VerifyEmailForm(BaseModel):
    token: str


@router.post("/verify-email", response_model=SigninResponse)
async def verify_email(request: Request, form_data: VerifyEmailForm):
    """Verify user email using verification token"""
    if not EMAIL_VERIFICATION_ENABLED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, 
            detail="Email verification is not enabled"
        )
    
    try:
        # Verify the token and get user
        user = Users.verify_user_email(form_data.token)
        if not user:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token"
            )
        
        # Also verify in Auth table
        Auths.verify_auth_email(form_data.token)
        
        # Send welcome email
        send_welcome_email(user.email, user.name)
        
        # Create login token for verified user
        token = create_token(
            data={"id": user.id},
            expires_delta=parse_duration(request.app.state.JWT_EXPIRES_IN),
        )
        
        return SigninResponse(
            token=token,
            token_type="Bearer",
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            profile_image_url=user.profile_image_url,
        )
        
    except Exception as e:
        logger.error(f"Email verification error: {e}")
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Email verification failed"
        )


class ResendVerificationForm(BaseModel):
    email: str


@router.post("/resend-verification")
async def resend_verification_email(request: Request, form_data: ResendVerificationForm):
    """Resend email verification"""
    if not EMAIL_VERIFICATION_ENABLED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Email verification is not enabled"
        )
    
    try:
        # Get user by email
        user = Users.get_user_by_email(form_data.email.lower())
        if not user:
            # Don't reveal if email exists for security
            return {"message": "如果该邮箱存在且未验证，验证邮件已发送。"}
        
        # Check if already verified
        if user.email_verified:
            return {"message": "该邮箱已验证。"}
        
        # Generate new verification token
        verification_token = generate_verification_token()
        
        # Update tokens in both tables
        Users.set_user_verification_token(user.id, verification_token)
        Auths.set_auth_verification_token(user.id, verification_token)
        
        # Send verification email
        if send_verification_email(user.email, user.name, verification_token):
            return {"message": "验证邮件已发送。"}
        else:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="验证邮件发送失败"
            )
            
    except Exception as e:
        logger.error(f"Resend verification error: {e}")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resend verification email"
        )
