"""
Billing Views
=============
6 API endpoints cho hệ thống billing:

1. PaymentCreateView  — POST /v1/api/partner/billing/payments      (Partner Auth)
2. PaymentListView    — GET  /v1/api/partner/billing/payments      (Partner Auth)
3. PaymentWebhookView — POST /v1/api/webhooks/payments/callback    (Webhook Auth)
4. BalanceView        — GET  /v1/api/partner/billing/balance       (Partner Auth)
5. TokenUsageListView — GET  /v1/api/partner/billing/token-usage   (Partner Auth)
6. TokenDeductView    — POST /internal/v1/api/billing/tokens/deduct (Internal Auth)
"""

import uuid
import logging
from decimal import Decimal

from django.db import transaction
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payment, TokenTransaction, Wallet
from .permissions import (
    InternalServiceAuthentication,
    PartnerJWTAuthentication,
    WebhookSignatureAuthentication,
)
from .serializers import (
    PaymentSerializer,
    TokenDeductSerializer,
    TokenTransactionSerializer,
    TopupRequestSerializer,
    WalletSerializer,
    WebhookPayloadSerializer,
)

logger = logging.getLogger(__name__)

# Tỷ lệ quy đổi: 1000đ = 100 Token
TOKEN_RATE_PER_VND = Decimal('0.1')  # 100 tokens / 1000 VND = 0.1 token/VND


class BillingPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


# =============================================================================
# Endpoint 1: Tạo yêu cầu nạp tiền (Topup Intent)
# POST /v1/api/partner/billing/payments
# =============================================================================
class PaymentCreateView(APIView):
    """
    Tạo yêu cầu nạp tiền mới.

    - Tính toán token_amount theo tỷ lệ 1000đ = 100 Token
    - Tạo bản ghi Payment với status='pending'
    - Trả về URL thanh toán giả lập (mock)
    """
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TopupRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        amount = serializer.validated_data['amount']
        payment_method = serializer.validated_data['payment_method']
        partner_id = request.user.partner_id

        # Tính số token tương ứng: 1000đ = 100 Token
        token_amount = int(amount * TOKEN_RATE_PER_VND)

        # Tạo transaction_id giả lập
        mock_transaction_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"

        # Tạo bản ghi Payment
        payment = Payment.objects.create(
            partner_id=partner_id,
            amount=amount,
            token_amount=token_amount,
            payment_method=payment_method,
            transaction_id=mock_transaction_id,
            status=Payment.Status.PENDING,
        )

        # Mock payment URL (trong production sẽ gọi API cổng thanh toán thật)
        payment_url = (
            f"https://payment.mock.orbitai.vn/pay"
            f"?txn={mock_transaction_id}"
            f"&amount={amount}"
        )

        logger.info(
            f"Payment created: {payment.id} | "
            f"Partner: {partner_id} | "
            f"Amount: {amount} VND → {token_amount} tokens"
        )

        return Response(
            {
                'payment_url': payment_url,
                'transaction_id': mock_transaction_id,
                'payment_id': str(payment.id),
                'amount': str(amount),
                'token_amount': token_amount,
            },
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Endpoint 2: Lấy lịch sử nạp tiền
# GET /v1/api/partner/billing/payments/list
# =============================================================================
class PaymentListView(APIView):
    """
    Lấy danh sách lịch sử nạp tiền của partner hiện tại.
    Hỗ trợ filter theo status và phân trang.
    """
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        partner_id = request.user.partner_id
        queryset = Payment.objects.filter(partner_id=partner_id)

        # Filter theo status nếu có
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Phân trang
        paginator = BillingPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = PaymentSerializer(page, many=True)

        return paginator.get_paginated_response(serializer.data)


# =============================================================================
# Endpoint 3: Webhook nhận kết quả thanh toán
# POST /v1/api/webhooks/payments/callback
# =============================================================================
class PaymentWebhookView(APIView):
    """
    Nhận callback từ cổng thanh toán.
    Xác thực bằng chữ ký HMAC-SHA256.

    Logic (trong transaction.atomic):
    1. Tìm Payment theo transaction_id
    2. Nếu status='pending' → cập nhật thành 'completed'
    3. Tìm hoặc tạo Wallet cho partner
    4. Cộng token_amount vào available_tokens
    """
    authentication_classes = [WebhookSignatureAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WebhookPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        txn_id = serializer.validated_data['transaction_id']
        txn_status = serializer.validated_data['status']

        with transaction.atomic():
            # Tìm Payment theo transaction_id
            try:
                payment = Payment.objects.select_for_update().get(
                    transaction_id=txn_id
                )
            except Payment.DoesNotExist:
                return Response(
                    {'error': f'Không tìm thấy giao dịch: {txn_id}'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Chỉ xử lý nếu payment đang ở trạng thái pending
            if payment.status != Payment.Status.PENDING:
                return Response(
                    {
                        'error': (
                            f'Giao dịch đã được xử lý. '
                            f'Trạng thái hiện tại: {payment.status}'
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            if txn_status == 'success':
                # Cập nhật payment thành completed
                payment.status = Payment.Status.COMPLETED
                payment.save(update_fields=['status', 'updated_at'])

                # Tìm hoặc tạo wallet cho partner
                wallet, created = Wallet.objects.select_for_update().get_or_create(
                    partner_id=payment.partner_id,
                    defaults={'available_tokens': 0, 'total_used': 0},
                )

                # Cộng token vào ví
                wallet.available_tokens += payment.token_amount
                wallet.save(update_fields=['available_tokens', 'updated_at'])

                logger.info(
                    f"Webhook success: TXN={txn_id} | "
                    f"Partner={payment.partner_id} | "
                    f"+{payment.token_amount} tokens | "
                    f"Balance={wallet.available_tokens}"
                )

            elif txn_status == 'failed':
                payment.status = Payment.Status.FAILED
                payment.save(update_fields=['status', 'updated_at'])

                logger.info(
                    f"Webhook failed: TXN={txn_id} | "
                    f"Partner={payment.partner_id}"
                )

        return Response(
            {'success': True},
            status=status.HTTP_200_OK,
        )


# =============================================================================
# Endpoint 4: Lấy số dư Token
# GET /v1/api/partner/billing/balance
# =============================================================================
class BalanceView(APIView):
    """
    Trả về số dư token hiện tại của partner.
    Nếu chưa có wallet thì trả về balance = 0.
    """
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        partner_id = request.user.partner_id
        wallet, created = Wallet.objects.get_or_create(partner_id=partner_id)
        
        data = WalletSerializer(wallet).data

        return Response(data, status=status.HTTP_200_OK)


# =============================================================================
# Endpoint 5: Lấy lịch sử trừ Token
# GET /v1/api/partner/billing/token-usage
# =============================================================================
class TokenUsageListView(APIView):
    """
    Lấy danh sách lịch sử tiêu hao token của partner hiện tại.
    Sắp xếp mới nhất lên đầu, có phân trang.
    """
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        partner_id = request.user.partner_id
        queryset = TokenTransaction.objects.filter(partner_id=partner_id)

        # Phân trang
        paginator = BillingPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = TokenTransactionSerializer(page, many=True)

        return paginator.get_paginated_response(serializer.data)


# =============================================================================
# Endpoint 6: Trừ Token (Internal API)
# POST /internal/v1/api/billing/tokens/deduct
# =============================================================================
class TokenDeductView(APIView):
    """
    API nội bộ để trừ token khi partner sử dụng dịch vụ.
    Chỉ dành cho các microservice khác gọi sang.

    Logic (trong transaction.atomic + select_for_update):
    1. Lấy Wallet của partner (lock row)
    2. Kiểm tra available_tokens >= tokens_used
    3. Trừ available_tokens, cộng total_used
    4. Tạo bản ghi TokenTransaction
    """
    authentication_classes = [InternalServiceAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TokenDeductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        partner_id = serializer.validated_data['partner_id']
        service_id = serializer.validated_data['service_id']
        tokens_used = serializer.validated_data['tokens_used']
        conversation_id = serializer.validated_data.get('conversation_id')

        with transaction.atomic():
            # Lấy wallet và lock row để tránh race condition
            try:
                wallet = Wallet.objects.select_for_update().get(
                    partner_id=partner_id
                )
            except Wallet.DoesNotExist:
                return Response(
                    {'error': 'Partner chưa có ví. Vui lòng nạp tiền trước.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Kiểm tra số dư
            if wallet.available_tokens < tokens_used:
                return Response(
                    {
                        'error': 'Số dư token không đủ.',
                        'available_tokens': wallet.available_tokens,
                        'requested': tokens_used,
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )

            # Trừ token và cộng dồn total_used
            wallet.available_tokens -= tokens_used
            wallet.total_used += tokens_used
            wallet.save(
                update_fields=['available_tokens', 'total_used', 'updated_at']
            )

            # Tạo bản ghi lịch sử tiêu hao token
            token_txn = TokenTransaction.objects.create(
                partner_id=partner_id,
                service_id=service_id,
                tokens_used=tokens_used,
                cost=Decimal('0'),  # Cost sẽ được tính bởi service gọi vào
                conversation_id=conversation_id,
            )

            logger.info(
                f"Token deducted: Partner={partner_id} | "
                f"Service={service_id} | "
                f"-{tokens_used} tokens | "
                f"Remaining={wallet.available_tokens}"
            )

        return Response(
            {
                'success': True,
                'remaining_tokens': wallet.available_tokens,
                'transaction_id': str(token_txn.id),
            },
            status=status.HTTP_200_OK,
        )
