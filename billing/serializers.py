"""
Billing Serializers
===================
Serializers cho các request/response payload của billing API.
Bao gồm cả validation logic cho input data.
"""

from decimal import Decimal

from rest_framework import serializers

from .models import Payment, TokenTransaction, Wallet


# =============================================================================
# Request Serializers (Input Validation)
# =============================================================================
class TopupRequestSerializer(serializers.Serializer):
    """
    Validate payload tạo yêu cầu nạp tiền.
    POST /v1/partner/billing/payments
    """
    amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal('10000'),
        help_text="Số tiền VNĐ (tối thiểu 10,000đ)",
    )
    payment_method = serializers.ChoiceField(
        choices=Payment.PaymentMethod.choices,
        help_text="Phương thức thanh toán: payos, stripe, bank_transfer",
    )


class WebhookPayloadSerializer(serializers.Serializer):
    """
    Validate payload từ cổng thanh toán gọi webhook.
    POST /v1/webhooks/payments/callback
    """
    transaction_id = serializers.CharField(
        max_length=255,
        help_text="Mã giao dịch từ cổng thanh toán",
    )
    status = serializers.ChoiceField(
        choices=['success', 'failed'],
        help_text="Kết quả thanh toán",
    )
    signature = serializers.CharField(
        help_text="Chữ ký HMAC-SHA256 từ cổng thanh toán",
    )


class TokenDeductSerializer(serializers.Serializer):
    """
    Validate payload trừ token từ internal service.
    POST /internal/v1/billing/tokens/deduct
    """
    partner_id = serializers.UUIDField(
        help_text="UUID của Partner cần trừ token",
    )
    service_id = serializers.CharField(
        max_length=255,
        help_text="Mã Service tiêu token",
    )
    tokens_used = serializers.IntegerField(
        min_value=1,
        help_text="Số token cần trừ (tối thiểu 1)",
    )
    conversation_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        help_text="ID cuộc hội thoại (tùy chọn)",
    )


# =============================================================================
# Response Serializers (Model Serializers)
# =============================================================================
class PaymentSerializer(serializers.ModelSerializer):
    """Serializer cho bản ghi Payment (response)."""

    class Meta:
        model = Payment
        fields = [
            'id',
            'partner_id',
            'amount',
            'token_amount',
            'payment_method',
            'transaction_id',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class WalletSerializer(serializers.ModelSerializer):
    """Serializer cho bản ghi Wallet (response)."""

    class Meta:
        model = Wallet
        fields = [
            'id',
            'partner_id',
            'available_tokens',
            'total_used',
            'updated_at',
        ]
        read_only_fields = fields


class TokenTransactionSerializer(serializers.ModelSerializer):
    """Serializer cho bản ghi TokenTransaction (response)."""

    class Meta:
        model = TokenTransaction
        fields = [
            'id',
            'partner_id',
            'service_id',
            'tokens_used',
            'cost',
            'conversation_id',
            'created_at',
        ]
        read_only_fields = fields
