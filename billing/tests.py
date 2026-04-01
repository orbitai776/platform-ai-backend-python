"""
Billing Unit Tests
==================
Comprehensive tests for the billing app covering:
- Authentication mechanisms (Partner JWT, Webhook Signature, Internal API Key)
- All 6 API endpoints
- Edge cases (insufficient balance, duplicate webhook, invalid tokens, etc.)

Chạy tests:  python manage.py test billing -v2
"""

import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Payment, TokenTransaction, Wallet


# =============================================================================
# Test Settings — dùng secret keys cố định cho tests
# =============================================================================
TEST_JWT_SECRET = 'test-jwt-secret-key'
TEST_PAYOS_KEY = 'test-payos-checksum-key'
TEST_INTERNAL_KEY = 'test-internal-service-key'


# =============================================================================
# Helper functions
# =============================================================================
def generate_partner_jwt(partner_id, role='partner', secret=TEST_JWT_SECRET):
    """Tạo Internal JWT giả lập cho Partner."""
    payload = {
        'uid': str(partner_id),
        'role': role,
    }
    return jwt.encode(payload, secret, algorithm='HS256')


def generate_webhook_signature(data, key=TEST_PAYOS_KEY):
    """Tạo chữ ký HMAC-SHA256 giả lập cho Webhook."""
    # Sắp xếp fields (trừ signature) và nối thành chuỗi
    sorted_keys = sorted(data.keys())
    data_string = '&'.join(f"{k}={data[k]}" for k in sorted_keys)
    return hmac.new(
        key=key.encode('utf-8'),
        msg=data_string.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).hexdigest()


# =============================================================================
# Test Authentication
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class PartnerJWTAuthTest(TestCase):
    """Tests cho Partner JWT Authentication."""

    def setUp(self):
        self.client = APIClient()
        self.partner_id = uuid.uuid4()
        self.url = '/v1/partner/billing/balance'

    def test_valid_jwt_returns_200(self):
        """JWT hợp lệ → truy cập thành công."""
        token = generate_partner_jwt(self.partner_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_missing_auth_header_returns_403(self):
        """Không có header Authorization → 403."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_invalid_jwt_returns_error(self):
        """JWT sai secret → 401/403."""
        token = generate_partner_jwt(self.partner_id, secret='wrong-secret')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.get(self.url)
        self.assertIn(response.status_code, [401, 403])

    def test_empty_bearer_token_returns_401(self):
        """Bearer token rỗng → 401."""
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ')
        response = self.client.get(self.url)
        self.assertIn(response.status_code, [401, 403])


@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class InternalServiceAuthTest(TestCase):
    """Tests cho Internal Service Authentication."""

    def setUp(self):
        self.client = APIClient()
        self.url = '/internal/v1/billing/tokens/deduct'

    def test_valid_api_key_accepted(self):
        """API key hợp lệ → request được xác thực (dù có thể fail vì logic khác)."""
        self.client.credentials(HTTP_X_INTERNAL_API_KEY=TEST_INTERNAL_KEY)
        response = self.client.post(
            self.url,
            data={
                'partner_id': str(uuid.uuid4()),
                'service_id': str(uuid.uuid4()),
                'tokens_used': 10,
            },
            format='json',
        )
        # 404 vì wallet chưa tồn tại, nhưng NOT 401 → auth đã pass
        self.assertNotEqual(response.status_code, 401)

    def test_invalid_api_key_returns_error(self):
        """API key sai → 401/403."""
        self.client.credentials(HTTP_X_INTERNAL_API_KEY='wrong-key')
        response = self.client.post(self.url, data={}, format='json')
        self.assertIn(response.status_code, [401, 403])

    def test_missing_api_key_returns_403(self):
        """Không có API key → 403."""
        response = self.client.post(self.url, data={}, format='json')
        self.assertEqual(response.status_code, 403)


# =============================================================================
# Endpoint 1: POST /v1/partner/billing/payments (Tạo yêu cầu nạp tiền)
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class PaymentCreateViewTest(TestCase):
    """Tests cho endpoint tạo yêu cầu nạp tiền."""

    def setUp(self):
        self.client = APIClient()
        self.partner_id = uuid.uuid4()
        token = generate_partner_jwt(self.partner_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.url = '/v1/partner/billing/payments'

    def test_create_payment_success(self):
        """Tạo payment thành công với amount hợp lệ."""
        response = self.client.post(
            self.url,
            data={'amount': '500000', 'payment_method': 'payos'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify response chứa payment_url và transaction_id
        self.assertIn('payment_url', data)
        self.assertIn('transaction_id', data)
        self.assertEqual(data['token_amount'], 50000)  # 500000 * 0.1

        # Verify Payment record được tạo trong DB
        self.assertEqual(Payment.objects.count(), 1)
        payment = Payment.objects.first()
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(payment.amount, Decimal('500000.00'))
        self.assertEqual(str(payment.partner_id), str(self.partner_id))

    def test_token_calculation_correct(self):
        """Tỷ lệ quy đổi: 1000đ = 100 Token."""
        response = self.client.post(
            self.url,
            data={'amount': '1000000', 'payment_method': 'stripe'},
            format='json',
        )
        data = response.json()
        # 1,000,000 VND * 0.1 = 100,000 tokens
        self.assertEqual(data['token_amount'], 100000)

    def test_amount_too_low_returns_400(self):
        """Amount < 10,000đ → validation error."""
        response = self.client.post(
            self.url,
            data={'amount': '5000', 'payment_method': 'payos'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_payment_method_returns_400(self):
        """Payment method không hợp lệ → validation error."""
        response = self.client.post(
            self.url,
            data={'amount': '100000', 'payment_method': 'bitcoin'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_fields_returns_400(self):
        """Thiếu required fields → validation error."""
        response = self.client.post(self.url, data={}, format='json')
        self.assertEqual(response.status_code, 400)


# =============================================================================
# Endpoint 2: GET /v1/partner/billing/payments/list (Lịch sử nạp tiền)
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class PaymentListViewTest(TestCase):
    """Tests cho endpoint lấy lịch sử nạp tiền."""

    def setUp(self):
        self.client = APIClient()
        self.partner_id = uuid.uuid4()
        token = generate_partner_jwt(self.partner_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.url = '/v1/partner/billing/payments/list'

        # Tạo sample payments
        for i in range(3):
            Payment.objects.create(
                partner_id=self.partner_id,
                amount=Decimal('100000'),
                token_amount=10000,
                payment_method='payos',
                transaction_id=f'TXN-TEST-{i}',
                status='completed' if i < 2 else 'pending',
            )

        # Tạo payment của partner khác (không nên xuất hiện)
        Payment.objects.create(
            partner_id=uuid.uuid4(),
            amount=Decimal('50000'),
            token_amount=5000,
            payment_method='stripe',
            transaction_id='TXN-OTHER',
        )

    def test_list_own_payments_only(self):
        """Chỉ trả về payments của partner hiện tại."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)

    def test_filter_by_status(self):
        """Filter theo status hoạt động đúng."""
        response = self.client.get(self.url, {'status': 'pending'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)

    def test_pagination_works(self):
        """Phân trang trả về đúng cấu trúc."""
        response = self.client.get(self.url)
        data = response.json()
        self.assertIn('count', data)
        self.assertIn('results', data)
        self.assertIn('next', data)
        self.assertIn('previous', data)


# =============================================================================
# Endpoint 3: POST /v1/webhooks/payments/callback (Webhook)
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class PaymentWebhookViewTest(TestCase):
    """Tests cho webhook nhận kết quả thanh toán."""

    def setUp(self):
        self.client = APIClient()
        self.partner_id = uuid.uuid4()
        self.url = '/v1/webhooks/payments/callback'

        # Tạo payment pending
        self.payment = Payment.objects.create(
            partner_id=self.partner_id,
            amount=Decimal('500000'),
            token_amount=50000,
            payment_method='payos',
            transaction_id='TXN-WEBHOOK-001',
            status='pending',
        )

    def _make_signed_request(self, txn_id, txn_status):
        """Helper: tạo webhook request với chữ ký hợp lệ."""
        data = {
            'transaction_id': txn_id,
            'status': txn_status,
        }
        signature = generate_webhook_signature(data)
        data['signature'] = signature
        return self.client.post(self.url, data=data, format='json')

    def test_webhook_success_credits_wallet(self):
        """Webhook success → cộng token vào wallet."""
        response = self._make_signed_request('TXN-WEBHOOK-001', 'success')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

        # Verify Payment status updated
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'completed')

        # Verify Wallet created and credited
        wallet = Wallet.objects.get(partner_id=self.partner_id)
        self.assertEqual(wallet.available_tokens, 50000)

    def test_webhook_failed_marks_payment_failed(self):
        """Webhook failed → đánh dấu payment failed, không cộng token."""
        response = self._make_signed_request('TXN-WEBHOOK-001', 'failed')
        self.assertEqual(response.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'failed')

        # Không tạo wallet
        self.assertFalse(Wallet.objects.filter(partner_id=self.partner_id).exists())

    def test_duplicate_webhook_returns_409(self):
        """Gọi webhook lần 2 cho cùng transaction → 409 Conflict."""
        self._make_signed_request('TXN-WEBHOOK-001', 'success')
        response = self._make_signed_request('TXN-WEBHOOK-001', 'success')
        self.assertEqual(response.status_code, 409)

    def test_unknown_transaction_returns_404(self):
        """Transaction ID không tồn tại → 404."""
        response = self._make_signed_request('TXN-UNKNOWN', 'success')
        self.assertEqual(response.status_code, 404)

    def test_invalid_signature_returns_403(self):
        """Chữ ký sai → 401/403."""
        data = {
            'transaction_id': 'TXN-WEBHOOK-001',
            'status': 'success',
            'signature': 'invalid-signature-here',
        }
        response = self.client.post(self.url, data=data, format='json')
        self.assertIn(response.status_code, [401, 403])

    def test_webhook_adds_to_existing_wallet(self):
        """Nếu wallet đã có sẵn → cộng thêm token."""
        # Tạo wallet có sẵn 10000 tokens
        Wallet.objects.create(
            partner_id=self.partner_id,
            available_tokens=10000,
            total_used=0,
        )

        response = self._make_signed_request('TXN-WEBHOOK-001', 'success')
        self.assertEqual(response.status_code, 200)

        wallet = Wallet.objects.get(partner_id=self.partner_id)
        self.assertEqual(wallet.available_tokens, 60000)  # 10000 + 50000


# =============================================================================
# Endpoint 4: GET /v1/partner/billing/balance (Số dư Token)
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class BalanceViewTest(TestCase):
    """Tests cho endpoint lấy số dư token."""

    def setUp(self):
        self.client = APIClient()
        self.partner_id = uuid.uuid4()
        token = generate_partner_jwt(self.partner_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.url = '/v1/partner/billing/balance'

    def test_balance_with_existing_wallet(self):
        """Partner có wallet → trả về balance thực."""
        Wallet.objects.create(
            partner_id=self.partner_id,
            available_tokens=5000,
            total_used=1500,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['available_tokens'], 5000)
        self.assertEqual(data['total_used'], 1500)

    def test_balance_without_wallet_returns_zero(self):
        """Partner chưa có wallet → trả về balance = 0."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['available_tokens'], 0)
        self.assertEqual(data['total_used'], 0)


# =============================================================================
# Endpoint 5: GET /v1/partner/billing/token-usage (Lịch sử trừ Token)
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class TokenUsageListViewTest(TestCase):
    """Tests cho endpoint lấy lịch sử trừ token."""

    def setUp(self):
        self.client = APIClient()
        self.partner_id = uuid.uuid4()
        token = generate_partner_jwt(self.partner_id)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        self.url = '/v1/partner/billing/token-usage'

        # Tạo sample token transactions
        for i in range(3):
            TokenTransaction.objects.create(
                partner_id=self.partner_id,
                service_id=uuid.uuid4(),
                tokens_used=100 + i,
                cost=Decimal('0.5'),
            )

        # Transaction của partner khác
        TokenTransaction.objects.create(
            partner_id=uuid.uuid4(),
            service_id=uuid.uuid4(),
            tokens_used=999,
            cost=Decimal('1.0'),
        )

    def test_list_own_transactions_only(self):
        """Chỉ trả về token transactions của partner hiện tại."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 3)

    def test_pagination_structure(self):
        """Response có đủ cấu trúc phân trang."""
        response = self.client.get(self.url)
        data = response.json()
        self.assertIn('count', data)
        self.assertIn('results', data)


# =============================================================================
# Endpoint 6: POST /internal/v1/billing/tokens/deduct (Trừ Token)
# =============================================================================
@override_settings(
    INTERNAL_JWT_SECRET=TEST_JWT_SECRET,
    PAYOS_CHECKSUM_KEY=TEST_PAYOS_KEY,
    INTERNAL_SERVICE_KEY=TEST_INTERNAL_KEY,
)
class TokenDeductViewTest(TestCase):
    """Tests cho endpoint trừ token (Internal API)."""

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_X_INTERNAL_API_KEY=TEST_INTERNAL_KEY)
        self.partner_id = uuid.uuid4()
        self.service_id = uuid.uuid4()
        self.url = '/internal/v1/billing/tokens/deduct'

        # Tạo wallet có sẵn 1000 tokens
        self.wallet = Wallet.objects.create(
            partner_id=self.partner_id,
            available_tokens=1000,
            total_used=500,
        )

    def test_deduct_success(self):
        """Trừ token thành công khi đủ số dư."""
        response = self.client.post(
            self.url,
            data={
                'partner_id': str(self.partner_id),
                'service_id': str(self.service_id),
                'tokens_used': 150,
                'conversation_id': 'conv-test-001',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['remaining_tokens'], 850)  # 1000 - 150

        # Verify DB
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_tokens, 850)
        self.assertEqual(self.wallet.total_used, 650)  # 500 + 150

        # Verify TokenTransaction created
        self.assertEqual(TokenTransaction.objects.count(), 1)
        txn = TokenTransaction.objects.first()
        self.assertEqual(txn.tokens_used, 150)
        self.assertEqual(txn.conversation_id, 'conv-test-001')

    def test_insufficient_balance_returns_402(self):
        """Số dư không đủ → HTTP 402 Payment Required."""
        response = self.client.post(
            self.url,
            data={
                'partner_id': str(self.partner_id),
                'service_id': str(self.service_id),
                'tokens_used': 9999,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 402)
        data = response.json()
        self.assertEqual(data['available_tokens'], 1000)
        self.assertEqual(data['requested'], 9999)

        # Verify wallet unchanged
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_tokens, 1000)

    def test_wallet_not_found_returns_404(self):
        """Partner chưa có wallet → 404."""
        response = self.client.post(
            self.url,
            data={
                'partner_id': str(uuid.uuid4()),  # Partner mới, chưa có wallet
                'service_id': str(self.service_id),
                'tokens_used': 10,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 404)

    def test_deduct_exact_balance(self):
        """Trừ đúng bằng số dư → success, balance = 0."""
        response = self.client.post(
            self.url,
            data={
                'partner_id': str(self.partner_id),
                'service_id': str(self.service_id),
                'tokens_used': 1000,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['remaining_tokens'], 0)

    def test_deduct_zero_tokens_returns_400(self):
        """tokens_used = 0 → validation error (min_value=1)."""
        response = self.client.post(
            self.url,
            data={
                'partner_id': str(self.partner_id),
                'service_id': str(self.service_id),
                'tokens_used': 0,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_fields_returns_400(self):
        """Thiếu required fields → 400."""
        response = self.client.post(
            self.url,
            data={'partner_id': str(self.partner_id)},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_multiple_deductions_cumulative(self):
        """Nhiều lần trừ liên tiếp → cộng dồn đúng."""
        for i in range(5):
            self.client.post(
                self.url,
                data={
                    'partner_id': str(self.partner_id),
                    'service_id': str(self.service_id),
                    'tokens_used': 100,
                },
                format='json',
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_tokens, 500)   # 1000 - 5*100
        self.assertEqual(self.wallet.total_used, 1000)         # 500 + 5*100
        self.assertEqual(TokenTransaction.objects.count(), 5)


# =============================================================================
# Model Tests
# =============================================================================
class WalletModelTest(TestCase):
    """Tests cho Wallet model."""

    def test_create_wallet(self):
        """Tạo wallet thành công."""
        wallet = Wallet.objects.create(
            partner_id=uuid.uuid4(),
            available_tokens=100,
        )
        self.assertIsNotNone(wallet.id)
        self.assertEqual(wallet.total_used, 0)

    def test_partner_id_unique(self):
        """partner_id phải unique."""
        pid = uuid.uuid4()
        Wallet.objects.create(partner_id=pid)
        with self.assertRaises(Exception):
            Wallet.objects.create(partner_id=pid)

    def test_str_representation(self):
        """__str__ trả về chuỗi có ý nghĩa."""
        pid = uuid.uuid4()
        wallet = Wallet.objects.create(partner_id=pid, available_tokens=42)
        self.assertIn('42', str(wallet))


class PaymentModelTest(TestCase):
    """Tests cho Payment model."""

    def test_default_status_pending(self):
        """Payment mới tạo có status = pending."""
        payment = Payment.objects.create(
            partner_id=uuid.uuid4(),
            amount=Decimal('100000'),
            token_amount=10000,
            payment_method='payos',
        )
        self.assertEqual(payment.status, 'pending')

    def test_transaction_id_unique(self):
        """transaction_id unique."""
        Payment.objects.create(
            partner_id=uuid.uuid4(),
            amount=Decimal('100000'),
            token_amount=10000,
            payment_method='payos',
            transaction_id='TXN-UNIQUE-001',
        )
        with self.assertRaises(Exception):
            Payment.objects.create(
                partner_id=uuid.uuid4(),
                amount=Decimal('50000'),
                token_amount=5000,
                payment_method='stripe',
                transaction_id='TXN-UNIQUE-001',
            )
