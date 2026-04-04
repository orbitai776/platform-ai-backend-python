"""
Billing URL Configuration
==========================
Maps all 6 billing endpoints to their respective views.

Partner-facing APIs:  /v1/partner/billing/...
Webhook:              /v1/webhooks/payments/...
Internal API:         /internal/v1/billing/...
"""

from django.urls import path

from . import views

urlpatterns = [
    # -----------------------------------------------------------------
    # Partner-facing APIs (yêu cầu Partner JWT Authentication)
    # -----------------------------------------------------------------
    # Endpoint 1 & 2: Tạo yêu cầu nạp tiền (POST) & Lịch sử nạp tiền (GET)
    path(
        'v1/partner/billing/payments',
        views.PaymentCreateView.as_view(),
        name='payment-create',
    ),
    path(
        'v1/partner/billing/payments/list',
        views.PaymentListView.as_view(),
        name='payment-list',
    ),

    # Endpoint 4: Lấy số dư Token
    path(
        'v1/partner/billing/balance',
        views.BalanceView.as_view(),
        name='billing-balance',
    ),

    # Endpoint 5: Lấy lịch sử trừ Token
    path(
        'v1/partner/billing/token-usage',
        views.TokenUsageListView.as_view(),
        name='token-usage-list',
    ),

    # -----------------------------------------------------------------
    # Webhook (xác thực bằng Signature)
    # -----------------------------------------------------------------
    # Endpoint 3: Webhook nhận kết quả thanh toán
    path(
        'v1/webhooks/payments/callback',
        views.PaymentWebhookView.as_view(),
        name='payment-webhook',
    ),

    # -----------------------------------------------------------------
    # Internal API (xác thực bằng X-Internal-API-Key)
    # -----------------------------------------------------------------
    # Endpoint 6: Trừ Token
    path(
        'internal/v1/billing/tokens/deduct',
        views.TokenDeductView.as_view(),
        name='token-deduct',
    ),
]
