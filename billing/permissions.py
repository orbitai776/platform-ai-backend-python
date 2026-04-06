"""
Custom Authentication Classes
=============================
Implements 3 authentication mechanisms following the "Zero Trust Gateway" pattern:

1. PartnerJWTAuthentication — For frontend/partner-facing APIs
2. WebhookSignatureAuthentication — For payment gateway webhooks
3. InternalServiceAuthentication — For internal microservice-to-microservice calls
"""

import hashlib
import hmac
import json
import logging

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

logger = logging.getLogger(__name__)


# =============================================================================
# Helper: Simple user-like object to attach to request.user
# =============================================================================
class AuthenticatedPartner:
    """
    Object nhẹ đại diện cho Partner đã xác thực.
    Gắn vào request.user để các view có thể truy cập partner_id và role.
    """

    def __init__(self, partner_id, role='partner'):
        self.partner_id = partner_id
        self.role = role

    @property
    def is_authenticated(self):
        return True

    def __str__(self):
        return f"Partner({self.partner_id}, role={self.role})"


class WebhookUser:
    """Object đại diện cho một webhook request đã verify thành công."""

    @property
    def is_authenticated(self):
        return True

    def __str__(self):
        return "WebhookUser"


class InternalServiceUser:
    """Object đại diện cho một internal service đã xác thực."""

    @property
    def is_authenticated(self):
        return True

    def __str__(self):
        return "InternalServiceUser"


# =============================================================================
# 1. Partner JWT Authentication (Dành cho API Frontend)
# =============================================================================
class PartnerJWTAuthentication(authentication.BaseAuthentication):
    """
    Xác thực Partner qua Internal JWT.

    Flow:
    - Gateway (NestJS) xác thực Firebase → tạo Internal JWT → gửi xuống.
    - Service Django verify lại Internal JWT bằng INTERNAL_JWT_SECRET.
    - Bóc tách `uid` (partner_id) và `role` từ JWT payload.

    Header: Authorization: Bearer <token>
    """

    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')

        if not auth_header.startswith('Bearer '):
            return None  # Không phải auth scheme này, để DRF thử scheme khác

        token = auth_header[7:]  # Bỏ prefix "Bearer "

        if not token:
            raise exceptions.AuthenticationFailed(
                'Token trống. Vui lòng cung cấp JWT hợp lệ.'
            )

        try:
            # Decode và verify JWT bằng secret key nội bộ
            payload = jwt.decode(
                token,
                settings.INTERNAL_JWT_SECRET,
                algorithms=['HS256'],
                options={'verify_aud': False},
            )
        except jwt.ExpiredSignatureError:
            raise exceptions.AuthenticationFailed('Token đã hết hạn.')
        except jwt.InvalidTokenError as e:
            logger.warning(f"JWT decode failed: {e}")
            raise exceptions.AuthenticationFailed(
                'Token không hợp lệ.'
            )

        # Lấy uid (partner_id) và role từ payload
        partner_id = payload.get('uid')
        role = payload.get('role', 'partner')

        if not partner_id:
            raise exceptions.AuthenticationFailed(
                'Token thiếu trường uid (partner_id).'
            )

        user = AuthenticatedPartner(partner_id=partner_id, role=role)
        return (user, token)


# =============================================================================
# 2. Webhook Signature Authentication (Dành cho Cổng thanh toán)
# =============================================================================
class WebhookSignatureAuthentication(authentication.BaseAuthentication):
    """
    Xác thực Webhook từ cổng thanh toán (PayOS, v.v.).

    Không sử dụng JWT. Thay vào đó verify chữ ký HMAC-SHA256:
    - Lấy tất cả fields trong body (trừ `signature`)
    - Sắp xếp theo key → nối thành chuỗi "key=value"
    - Tính HMAC-SHA256 với PAYOS_CHECKSUM_KEY
    - So sánh kết quả với `signature` trong body
    """

    def authenticate(self, request):
        # Chỉ áp dụng cho POST request tới webhook endpoint
        if request.method != 'POST':
            return None

        try:
            body = request.data
        except Exception:
            raise exceptions.AuthenticationFailed(
                'Không thể parse request body.'
            )

        signature = body.get('signature')
        if not signature:
            raise exceptions.AuthenticationFailed(
                'Thiếu chữ ký (signature) trong request body.'
            )

        # Tạo chuỗi data để verify: sắp xếp các field (trừ signature)
        data_fields = {
            k: v for k, v in body.items() if k != 'signature'
        }
        sorted_keys = sorted(data_fields.keys())
        data_string = '&'.join(
            f"{k}={data_fields[k]}" for k in sorted_keys
        )

        # Tính HMAC-SHA256
        expected_signature = hmac.new(
            key=settings.PAYOS_CHECKSUM_KEY.encode('utf-8'),
            msg=data_string.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).hexdigest()

        # So sánh chữ ký (constant-time comparison để tránh timing attack)
        if not hmac.compare_digest(expected_signature, signature):
            logger.warning(
                f"Webhook signature mismatch. "
                f"Expected: {expected_signature}, Got: {signature}"
            )
            raise exceptions.AuthenticationFailed(
                'Chữ ký không hợp lệ.'
            )

        return (WebhookUser(), None)


# =============================================================================
# 3. Internal Service Authentication (Dành cho Microservice gọi sang)
# =============================================================================
class InternalServiceAuthentication(authentication.BaseAuthentication):
    """
    Xác thực Internal API call từ các microservice khác (NodeJS).

    Header: X-Internal-API-Key: <key>
    So sánh với INTERNAL_SERVICE_KEY trong settings.
    """

    def authenticate(self, request):
        api_key = request.META.get('HTTP_X_INTERNAL_API_KEY', '')

        if not api_key:
            return None  # Không phải auth scheme này

        if not hmac.compare_digest(api_key, settings.INTERNAL_SERVICE_KEY):
            raise exceptions.AuthenticationFailed(
                'Internal API Key không hợp lệ.'
            )

        return (InternalServiceUser(), None)
