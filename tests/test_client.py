from fints.client import FinTS3PinTanClient, TransactionResponse, NeedTANResponse, ResponseStatus, NeedRetryResponse, FinTSOperations
from fints.exceptions import FinTSClientPINError, FinTSClientTemporaryAuthError
from decimal import Decimal
import datetime
import pytest


@pytest.fixture
def fints_client(fints_server):
    return FinTS3PinTanClient(
        '12345678',
        'test1',
        '1234',
        fints_server,
        product_id="TEST-123", product_version="1.2.3",
    )


@pytest.mark.parametrize('result_code, confirmation_required, polled', [
    ('RCVC', False, False),
    ('RCVC', True, False),
    ('RCVC', False, True),
    ('RVNM', False, False),
    ('RVMC', False, False),
    ('RVNA', False, False),
])
def test_vop_full_match_keeps_existing_tan_challenge(
    monkeypatch, result_code, confirmation_required, polled
):
    from types import SimpleNamespace
    from fints.client import HIVPP1, NeedVOPResponse

    command = SimpleNamespace(header=SimpleNamespace(type='HKCSE'))
    tan = object()
    final_vop = SimpleNamespace(
        vop_id=b'check-id', polling_id=None,
        vop_single_result=SimpleNamespace(result=result_code),
    )
    initial_vop = SimpleNamespace(
        vop_id=None, polling_id=b'poll-id', wait_for_seconds=1,
        vop_single_result=SimpleNamespace(result='PDNG'),
    ) if polled else final_vop
    codes = ['3040'] if polled else ['3945'] if confirmation_required else ['3955']
    replies = [SimpleNamespace(code=code, parameters=['cursor']) for code in codes]

    class Reply:
        def __init__(self, vop):
            self.vop = vop

        def find_segment_first(self, kind, throw=False):
            if kind is HIVPP1:
                return self.vop
            return SimpleNamespace(challenge='Approve in the app')

        def responses(self, segment):
            return replies if segment is tan else []

        def find_segments(self, kinds):
            return [SimpleNamespace(responses=replies)]

    class Dialog:
        calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send(self, *segments):
            self.calls += 1
            return Reply(initial_vop if self.calls == 1 else final_vop)

    client = FinTS3PinTanClient.__new__(FinTS3PinTanClient)
    client._find_vop_format_for_segment = lambda command: 'sepade.pain.002.001.10.xsd'
    client._need_twostep_tan_for_segment = lambda command: True
    client._get_tan_segment = lambda *args: tan
    client.is_challenge_structured = lambda: False
    monkeypatch.setattr('time.sleep', lambda seconds: None)
    dialog = Dialog()

    response = client._send_pay_with_possible_retry(dialog, command, lambda *args: None)

    if result_code == 'RCVC' and not confirmation_required and not polled:
        assert isinstance(response, NeedTANResponse)
        assert response.decoupled is True
        assert response.vop_result is final_vop
    else:
        assert isinstance(response, NeedVOPResponse)
        assert response.vop_result is final_vop
    assert dialog.calls == (2 if polled else 1)


def test_get_sepa_accounts(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()

    assert accounts


def test_get_information(fints_client):
    with fints_client:
        information = fints_client.get_information()

    assert information["bank"]["name"] == 'Test Bank'
    assert information["bank"]["supported_operations"][FinTSOperations.SEPA_TRANSFER_SINGLE]
    assert information["bank"]["supported_operations"][FinTSOperations.SEPA_TRANSFER_SINGLE_SCHEDULED]


def test_pin_wrong(fints_server):
    client = FinTS3PinTanClient(
        '12345678',
        'test1',
        '99999',
        fints_server,
        product_id="TEST-123", product_version="1.2.3",
    )
    with pytest.raises(FinTSClientPINError):
        with client:
            pass

    assert client.pin.blocked

    with pytest.raises(Exception):
        with client:
            pass

    with pytest.raises(Exception, match="Refusing"):
        str(client.pin)


def test_pin_locked(fints_server):
    client = FinTS3PinTanClient(
        '12345678',
        'test1',
        '3938',
        fints_server,
        product_id="TEST-123", product_version="1.2.3",
    )
    with pytest.raises(FinTSClientTemporaryAuthError):
        with client:
            pass

    assert client.pin.blocked

    with pytest.raises(Exception):
        with client:
            pass

    with pytest.raises(Exception, match="Refusing"):
        str(client.pin)


def test_resume(fints_client, fints_server):
    with fints_client:
        system_id = fints_client.system_id
        dialog_id = fints_client._standing_dialog.dialog_id
        assert fints_client.bpd_version == 78

        d_data = fints_client.pause_dialog()

    c_data = fints_client.deconstruct(including_private=True)

    FinTS3PinTanClient(
        '12345678',
        'test1',
        '1234',
        fints_server,
        from_data=c_data,
        product_id="TEST-123", product_version="1.2.3",
    )
    assert system_id == fints_client.system_id

    with fints_client.resume_dialog(d_data):
        assert dialog_id == fints_client._standing_dialog.dialog_id
        assert fints_client.bpd_version == 78


def test_transfer_1step(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('1.23'),
            'Test Sender',
            'Test transfer 1step',
        )

        assert isinstance(a, TransactionResponse)
        assert a.status == ResponseStatus.SUCCESS

        assert a.responses[0].code == '0010'
        assert a.responses[0].text == "Transfer 1.23 to DE111234567800000002 re 'Test transfer 1step'"


def test_scheduled_transfer_1step(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_scheduled_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('20.00'),
            'Test Sender',
            'Test scheduled transfer 1step',
            datetime.date(2026, 6, 19),
        )

        assert isinstance(a, TransactionResponse)
        assert a.status == ResponseStatus.SUCCESS

        assert a.data["order_id"].startswith("ORDER-")
        assert a.responses[0].code == '0010'
        assert a.responses[0].text == "Scheduled 20.00 on 2026-06-19"


def test_transfer_1step_regression(fints_client):
    # Passing a float is not officially supported, but should not fail with wrong values
    # Decimal(1.23) is Decimal('1.229999999999999982236431605997495353221893310546875')
    # which emphatically should not be cut to 122 cents.

    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            1.23,
            'Test Sender',
            'Test transfer 1step'
        )

        assert isinstance(a, TransactionResponse)
        assert a.responses[0].text == "Transfer 1.23 to DE111234567800000002 re 'Test transfer 1step'"


def test_transfer_2step(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('2.34'),
            'Test Sender',
            'Test transfer 2step'
        )

        assert isinstance(a, NeedTANResponse)

        b = fints_client.send_tan(a, '123456')
        assert b.status == ResponseStatus.SUCCESS
        assert b.responses[0].text == "Transfer 2.34 to DE111234567800000002 re 'Test transfer 2step'"


def test_transfer_2step_continue(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('3.42'),
            'Test Sender',
            'Test transfer 2step'
        )

        a_data = a.get_data()

        a_prime = NeedRetryResponse.from_data(a_data)

        b = fints_client.send_tan(a_prime, '123456')
        assert b.status == ResponseStatus.SUCCESS
        assert b.responses[0].text == "Transfer 3.42 to DE111234567800000002 re 'Test transfer 2step'"


def test_transfer_2step_continue_preserves_decoupled(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        challenge = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('3.42'),
            'Test Sender',
            'Test transfer 2step'
        )
        challenge.decoupled = True

        restored_challenge = NeedRetryResponse.from_data(challenge.get_data())

        assert restored_challenge.decoupled is True


def test_tan_wrong(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('3.33'),
            'Test Sender',
            'Test transfer 2step'
        )

        b = fints_client.send_tan(a, '99881')
        assert b.status == ResponseStatus.ERROR


def test_tan_hhduc(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()
        a = fints_client.simple_sepa_transfer(
            accounts[0],
            'DE111234567800000002',
            'GENODE23X42',
            'Test Receiver',
            Decimal('5.23'),
            'Test Sender',
            'Test transfer hhduc 2step'
        )

        from fints.hhd.flicker import parse
        assert a.challenge == 'Geben Sie den Startcode als TAN an'
        flicker = parse(a.challenge_hhduc)

        b = fints_client.send_tan(a, flicker.startcode.data)
        assert b.status == ResponseStatus.SUCCESS


def test_get_transactions(fints_client):
    with fints_client:
        accounts = fints_client.get_sepa_accounts()

        transactions = fints_client.get_transactions(accounts[0])

        assert len(transactions) == 3
        assert transactions[0].data['amount'].amount == Decimal('182.34')
