"""
Billing Models
==============
Defines 3 core tables for the billing system:
- Wallet: Token balance per partner
- Payment: Top-up payment history
- TokenTransaction: Token consumption history

NOTE: partner_id and service_id are "soft links" (UUID stored as field, NOT foreign keys)
because the referenced tables live in other microservices.
"""

import uuid
from django.db import models


class Wallet(models.Model):
    """
    Ví lưu trữ Token cho mỗi Partner.
    Mỗi partner chỉ có duy nhất 1 wallet (partner_id unique).
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    # Soft link — không dùng ForeignKey vì bảng Partner ở service khác
    partner_id = models.UUIDField(
        unique=True,
        db_index=True,
        help_text="UUID của Partner (soft link đến Partner Service)",
    )
    available_tokens = models.IntegerField(
        default=0,
        help_text="Số token khả dụng hiện tại",
    )
    total_used = models.IntegerField(
        default=0,
        help_text="Tổng số token đã sử dụng",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_wallet'
        verbose_name = 'Wallet'
        verbose_name_plural = 'Wallets'

    def __str__(self):
        return f"Wallet({self.partner_id}) - {self.available_tokens} tokens"


class Payment(models.Model):
    """
    Lịch sử nạp tiền (Top-up).
    Mỗi bản ghi tương ứng với một lần nạp tiền qua cổng thanh toán.
    """

    class PaymentMethod(models.TextChoices):
        PAYOS = 'payos', 'PayOS'
        STRIPE = 'stripe', 'Stripe'
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    partner_id = models.UUIDField(
        db_index=True,
        help_text="UUID của Partner (soft link)",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Số tiền VNĐ",
    )
    token_amount = models.IntegerField(
        help_text="Số token được quy đổi tương ứng",
    )
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        help_text="Phương thức thanh toán",
    )
    transaction_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Mã giao dịch từ cổng thanh toán",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'billing_payment'
        ordering = ['-created_at']
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'

    def __str__(self):
        return (
            f"Payment({self.id}) - {self.amount} VND "
            f"({self.token_amount} tokens) [{self.status}]"
        )


class TokenTransaction(models.Model):
    """
    Lịch sử tiêu hao Token.
    Mỗi bản ghi được tạo khi một microservice gọi Internal API để trừ token.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    partner_id = models.UUIDField(
        db_index=True,
        help_text="UUID của Partner (soft link)",
    )
    service_id = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Mã định danh của Service tiêu token (vd: chatbot-ai-01)",
    )
    tokens_used = models.IntegerField(
        help_text="Số token đã sử dụng",
    )
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text="Chi phí tương ứng",
    )
    conversation_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="ID cuộc hội thoại liên quan (nếu có)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'billing_token_transaction'
        ordering = ['-created_at']
        verbose_name = 'Token Transaction'
        verbose_name_plural = 'Token Transactions'

    def __str__(self):
        return (
            f"TokenTransaction({self.id}) - "
            f"{self.tokens_used} tokens by {self.partner_id}"
        )
