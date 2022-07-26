import unittest
from decimal import Decimal
from unittest.mock import ANY

from algojig import TealishProgram, get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction
from algosdk.logic import get_application_address

addr = 'RTR6MP4VKLZRBLKTNWR4PDH5QGMQYVDRQ6OSBEYR6OJLK7W2YKY2HFGKLE'
sk = 'vJB2vFVww2xs7fvfZcr8LQTWkGO5MEwS+jwfRfzcoZeM4+Y/lVLzEK1TbaPHjP2BmQxUcYedIJMR85K1ftrCsQ=='

logicsig = TealishProgram('contracts/pool_template.tl')
approval_program = TealishProgram('contracts/amm_approval.tl')

METHOD_BOOTSTRAP = "bootstrap"
METHOD_ADD_LIQUIDITY = "add_liquidity"
METHOD_REMOVE_LIQUIDITY = "remove_liquidity"
METHOD_SWAP = "swap"
POOLERS_FEE_SHARE = 25
PROTOCOL_FEE_SHARE = 5
LOCKED_POOL_TOKENS = 1_000

MAX_ASSET_AMOUNT = 18446744073709551615
POOL_TOKEN_TOTAL_SUPPLY = MAX_ASSET_AMOUNT
ALGO_ASSET_ID = 0
APPLICATION_ID = 1
APPLICATION_ADDRESS = get_application_address(APPLICATION_ID)
print('App Address:', APPLICATION_ADDRESS)


def get_pool_logicsig_bytecode(asset_1_id, asset_2_id, fee_tier=3):
    # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
    template = b'\x06\x80 \x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
    program = bytearray(logicsig.bytecode)

    # Algo SDK doesn't support teal version 6 at the moment
    program[0:1] = (6).to_bytes(1, "big")
    assert program == bytearray(template)

    program[3:11] = (APPLICATION_ID).to_bytes(8, 'big')
    program[11:19] = asset_1_id.to_bytes(8, 'big')
    program[19:27] = asset_2_id.to_bytes(8, 'big')
    program[27:35] = fee_tier.to_bytes(8, 'big')
    return transaction.LogicSigAccount(program)


# lsig = get_pool_logicsig_bytecode(5, 2)
# pool_address = lsig.address()
# print('Pool Address:', pool_address)

class BaseTestCase(unittest.TestCase):
    maxDiff = None

    def opt_in_asset(self, address, asset_id):
        self.ledger.set_account_balance(address, 0, asset_id=asset_id)

    def ledger_transfer(self, amount, asset_id=0, sender=None, receiver=None):
        assert sender or receiver

        if receiver:
            receiver_balance, _ = self.ledger.accounts[receiver]['balances'].get(asset_id)
            self.ledger.set_account_balance(receiver, receiver_balance + amount, asset_id=asset_id)

        if sender:
            sender_balance, _ = self.ledger.accounts[sender]['balances'].get(asset_id)
            self.ledger.set_account_balance(sender, sender_balance - amount, asset_id=asset_id)

    def bootstrap_pool(self):
        asset_2_id = getattr(self, "asset_2_id", ALGO_ASSET_ID)

        # Set Algo balance
        self.ledger.set_account_balance(self.pool_address, 1_000_000)

        # Rekey to application address
        self.ledger.set_auth_addr(self.pool_address, APPLICATION_ADDRESS)

        # Opt-in to assets
        self.ledger.set_account_balance(self.pool_address, 0, asset_id=self.asset_1_id)
        if asset_2_id != 0:
            self.ledger.set_account_balance(self.pool_address, 0, asset_id=self.asset_2_id)

        # Create pool token
        self.pool_token_asset_id = self.ledger.create_asset(asset_id=None, params=dict(creator=APPLICATION_ADDRESS))

        # Transfer Algo to application address
        self.ledger.set_account_balance(APPLICATION_ADDRESS, 200_000)

        # Transfer pool tokens from application adress to pool
        self.ledger.set_account_balance(APPLICATION_ADDRESS, 0, asset_id=self.pool_token_asset_id)
        self.ledger.set_account_balance(self.pool_address, POOL_TOKEN_TOTAL_SUPPLY, asset_id=self.pool_token_asset_id)

        self.ledger.set_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state={
                b'asset_1_id': self.asset_1_id,
                b'asset_2_id': asset_2_id,
                b'fee_tier': self.fee_tier,
                b'pool_token_asset_id': self.pool_token_asset_id,
                b'poolers_fee_share': POOLERS_FEE_SHARE,
                b'protocol_fee_share': PROTOCOL_FEE_SHARE,
            }
        )

    def set_initial_pool_liquidity(self, asset_1_reserves, asset_2_reserves, liquidity_provider_address=None):
        issued_pool_token_amount = int(Decimal.sqrt(Decimal(asset_1_reserves) * Decimal(asset_2_reserves)))
        pool_token_out_amount = issued_pool_token_amount - LOCKED_POOL_TOKENS
        assert pool_token_out_amount > 0

        # TODO: Add update_local_state method to AlgoJig
        self.ledger.set_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state={
                **self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID],
                b'asset_1_reserves': asset_1_reserves,
                b'asset_2_reserves': asset_2_reserves,
                b'issued_pool_tokens': issued_pool_token_amount
            }
        )

        self.ledger_transfer(sender=liquidity_provider_address, receiver=self.pool_address, amount=asset_1_reserves, asset_id=self.asset_1_id)
        self.ledger_transfer(sender=liquidity_provider_address, receiver=self.pool_address, amount=asset_2_reserves, asset_id=self.asset_2_id)
        self.ledger_transfer(sender=self.pool_address, receiver=liquidity_provider_address, amount=pool_token_out_amount, asset_id=self.pool_token_asset_id)

    def get_add_liquidity_transactions(self, asset_1_amount, asset_2_amount, app_call_fee=None):
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_amount,
            ),
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=asset_2_amount,
            ) if self.asset_2_id else transaction.PaymentTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=asset_2_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_ADD_LIQUIDITY],
                foreign_assets=[self.asset_1_id, self.asset_2_id, self.pool_token_asset_id] if self.asset_2_id else [self.asset_1_id, self.pool_token_asset_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[2].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_remove_liquidity_transactions(self, liquidity_asset_amount, app_call_fee=None):
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.pool_token_asset_id,
                amt=liquidity_asset_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_REMOVE_LIQUIDITY],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = app_call_fee or self.sp.fee
        return txn_group

    @classmethod
    def sign_txns(cls, txns):
        return [txn.sign(sk)for txn in txns]


class TestBootstrap(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.minimum_fee = 7000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.fee_tier = 3
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USD"))
        self.ledger.create_asset(self.asset_2_id, params=dict(unit_name="BTC"))
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_2_id)

    def test_pass(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        block = self.ledger.eval_transactions(transactions)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertEqual(
            txn[b'txn'],
            {
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), self.asset_2_id.to_bytes(8, "big"), self.fee_tier.to_bytes(8, "big")],
                b'apan': transaction.OnComplete.OptInOC,
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apid': APPLICATION_ID,
                b'fee': self.minimum_fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rekey': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 6)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': 200000,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rcv': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'pay'
            }
        )

        # inner transactions - [1]
        created_asset_id = inner_transactions[1][b'caid']

        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'apar': {
                    b'an': b'TinymanPool2.0 USD-BTC',
                    b'au': b'https://tinyman.org',
                    b'dc': 6,
                    b't': self.pool_token_total_supply,
                    b'un': b'TMPOOL2'
                },
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'acfg'
            }
        )

        # inner transactions - [2]
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            }
        )

        # inner transactions - [3]
        self.assertDictEqual(
            inner_transactions[3][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            }
        )

        # inner transactions - [4]
        self.assertDictEqual(
            inner_transactions[4][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # inner transactions - [5]
        self.assertDictEqual(
            inner_transactions[5][b'txn'],
            {
                b'aamt': 18446744073709551615,
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # local state delta
        pool_delta = txn[b'dt'][b'ld'][0]
        self.assertDictEqual(
            pool_delta,
            {
                b'asset_1_id': {b'at': 2, b'ui': self.asset_1_id},
                b'asset_2_id': {b'at': 2, b'ui': self.asset_2_id},
                b'fee_tier': {b'at': 2, b'ui': self.fee_tier},
                b'pool_token_asset_id': {b'at': 2, b'ui': created_asset_id},
                b'poolers_fee_share': {b'at': 2, b'ui': POOLERS_FEE_SHARE},
                b'protocol_fee_share': {b'at': 2, b'ui': PROTOCOL_FEE_SHARE}
            }
        )

    def test_fail_rekey(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)

        # TODO: Isn't this transaction rejected by the pool logic sig?
        # Rekey is missing
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(Txn.RekeyTo == Global.CurrentApplicationAddress)')

        # Rekey address is wrong
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=generate_account()[1],
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(Txn.RekeyTo == Global.CurrentApplicationAddress)')

    def test_fail_wrong_ids_for_logicsig(self):
        wrong_asset_1_id = self.asset_1_id + 1
        lsig = get_pool_logicsig_bytecode(wrong_asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(hash == Txn.Sender)')

    def test_fail_wrong_asset_order(self):
        lsig = get_pool_logicsig_bytecode(self.asset_2_id, self.asset_1_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_2_id, self.asset_1_id, self.fee_tier],
                    foreign_assets=[self.asset_2_id, self.asset_1_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_id > asset_2_id)')

    def test_fail_different_assets_are_included_in_application_args_and_foreign_assets(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id + 9999, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_id == Txn.Assets[0])')

        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id + 9999],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(asset_2_id == Txn.Assets[1])')

    def test_fail_insufficient_fee(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = self.minimum_fee - 1

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')

    def test_fail_wrong_method_name(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["invalid", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], f'assert(Txn.ApplicationArgs[0] == "{METHOD_BOOTSTRAP}")')


class TestBootstrapAlgoPair(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.minimum_fee = 6000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.asset_2_id = ALGO_ASSET_ID
        cls.fee_tier = 3
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USD"))
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_1_id)

    def test_pass(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, ALGO_ASSET_ID)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, ALGO_ASSET_ID, self.fee_tier],
                    foreign_assets=[self.asset_1_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        block = self.ledger.eval_transactions(transactions)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertEqual(
            txn[b'txn'],
            {
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), ALGO_ASSET_ID.to_bytes(8, "big"), self.fee_tier.to_bytes(8, "big")],
                b'apan': transaction.OnComplete.OptInOC,
                b'apas': [self.asset_1_id],
                b'apid': APPLICATION_ID,
                b'fee': self.minimum_fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rekey': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 5)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': 200000,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rcv': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'pay'
            }
        )

        # inner transactions - [1]
        created_asset_id = inner_transactions[1][b'caid']
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'apar': {
                    b'an': b'TinymanPool2.0 USD-ALGO',
                    b'au': b'https://tinyman.org',
                    b'dc': 6,
                    b't': self.pool_token_total_supply,
                    b'un': b'TMPOOL2'
                },
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'acfg'
            }
        )

        # inner transactions - [2]
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            }
        )

        # inner transactions - [3]
        self.assertDictEqual(
            inner_transactions[3][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # inner transactions - [4]
        self.assertDictEqual(
            inner_transactions[4][b'txn'],
            {
                b'aamt': 18446744073709551615,
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # local state delta
        pool_delta = txn[b'dt'][b'ld'][0]
        self.assertDictEqual(
            pool_delta,
            {
                b'asset_1_id': {b'at': 2, b'ui': self.asset_1_id},
                b'asset_2_id': {b'at': 2},      # b'ui': ALGO_ASSET_ID
                b'fee_tier': {b'at': 2, b'ui': self.fee_tier},
                b'pool_token_asset_id': {b'at': 2, b'ui': created_asset_id},
                b'poolers_fee_share': {b'at': 2, b'ui': POOLERS_FEE_SHARE},
                b'protocol_fee_share': {b'at': 2, b'ui': PROTOCOL_FEE_SHARE}
            }
        )


class TestAddLiquidity(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.fee_tier = 3

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.set_account_balance(addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.opt_in_asset(addr, self.pool_token_asset_id)

    def setUp(self):
        self.reset_ledger()

    def test_initial_add_liqiudity(self):
        test_cases = [
            dict(
                msg="Test adding initial liquidy (basic pass).",
                inputs=dict(
                    asset_1_added_liquidity_amount=1_000_000,
                    asset_2_added_liquidity_amount=1_000_000,
                ),
                outputs=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                    pool_tokens_out_amount=1_000_000 - LOCKED_POOL_TOKENS
                )
            ),
            dict(
                msg="Test pool token amount is rounded down.",
                inputs=dict(
                    asset_1_added_liquidity_amount=10_000,
                    asset_2_added_liquidity_amount=15_000,
                ),
                outputs=dict(
                    asset_1_reserves=10_000,
                    asset_2_reserves=15_000,
                    issued_pool_token_amount=12247,
                    pool_tokens_out_amount=12247 - LOCKED_POOL_TOKENS
                )
            ),
            dict(
                msg="Test adding minumum liquidity.",
                inputs=dict(
                    asset_1_added_liquidity_amount=LOCKED_POOL_TOKENS + 1,
                    asset_2_added_liquidity_amount=LOCKED_POOL_TOKENS + 1,
                ),
                outputs=dict(
                    asset_1_reserves=LOCKED_POOL_TOKENS + 1,
                    asset_2_reserves=LOCKED_POOL_TOKENS + 1,
                    issued_pool_token_amount=LOCKED_POOL_TOKENS + 1,
                    pool_tokens_out_amount=1
                )
            ),
            dict(
                msg="Test overflow with adding high liquidity.",
                inputs=dict(
                    asset_1_added_liquidity_amount=MAX_ASSET_AMOUNT,
                    asset_2_added_liquidity_amount=MAX_ASSET_AMOUNT,
                ),
                outputs=dict(
                    asset_1_reserves=MAX_ASSET_AMOUNT,
                    asset_2_reserves=MAX_ASSET_AMOUNT,
                    issued_pool_token_amount=MAX_ASSET_AMOUNT,
                    pool_tokens_out_amount=MAX_ASSET_AMOUNT - LOCKED_POOL_TOKENS
                )
            ),
            dict(
                msg="Test pool token out is 0.",
                inputs=dict(
                    asset_1_added_liquidity_amount=LOCKED_POOL_TOKENS,
                    asset_2_added_liquidity_amount=LOCKED_POOL_TOKENS,
                ),
                exception=dict(
                    source_line='assert(pool_tokens_out)',
                )
            ),
            dict(
                msg="Test pool token out is negative.",
                inputs=dict(
                    asset_1_added_liquidity_amount=LOCKED_POOL_TOKENS - 1,
                    asset_2_added_liquidity_amount=LOCKED_POOL_TOKENS - 1,
                ),
                exception=dict(
                    source_line='pool_tokens_out = issued_pool_tokens - LOCKED_POOL_TOKENS',
                )
            ),
        ]

        for test_case in test_cases:
            with self.subTest(**test_case):
                self.reset_ledger()

                inputs = test_case["inputs"]
                txn_group = self.get_add_liquidity_transactions(asset_1_amount=inputs["asset_1_added_liquidity_amount"], asset_2_amount=inputs["asset_2_added_liquidity_amount"], app_call_fee=2_000)
                txn_group = transaction.assign_group_id(txn_group)
                stxns = self.sign_txns(txn_group)

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        block = self.ledger.eval_transactions(stxns)

                    self.assertEqual(e.exception.source['line'], exception.get("source_line"))

                else:
                    outputs = test_case["outputs"]

                    block = self.ledger.eval_transactions(stxns)
                    block_txns = block[b'txns']

                    # outer transactions
                    self.assertEqual(len(block_txns), 3)

                    # outer transactions - [0]
                    txn = block_txns[0]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'aamt': inputs["asset_1_added_liquidity_amount"],
                            b'arcv': decode_address(self.pool_address),
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'axfer',
                            b'xaid': self.asset_1_id
                        }
                    )

                    # outer transactions - [1]
                    txn = block_txns[1]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'aamt': inputs["asset_2_added_liquidity_amount"],
                            b'arcv': decode_address(self.pool_address),
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'axfer',
                            b'xaid': self.asset_2_id
                        }
                    )

                    # outer transactions - [2]
                    txn = block_txns[2]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'apaa': [b'add_liquidity'],
                            b'apas': [self.asset_1_id, self.asset_2_id, self.pool_token_asset_id],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee * 2,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'appl'
                        }
                    )

                    # inner transactions
                    inner_transactions = txn[b'dt'][b'itx']
                    self.assertEqual(len(inner_transactions), 1)

                    # inner transactions[0]
                    self.assertDictEqual(
                        inner_transactions[0][b'txn'],
                        {
                            b'aamt': outputs["pool_tokens_out_amount"],
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'arcv': decode_address(addr),
                            b'snd': decode_address(self.pool_address),
                            b'type': b'axfer',
                            b'xaid': self.pool_token_asset_id
                        }
                    )

                    # local state delta
                    pool_local_state_delta = txn[b'dt'][b'ld'][1]
                    self.assertDictEqual(
                        pool_local_state_delta,
                        {
                            b'asset_1_reserves': {b'at': 2, b'ui': outputs["asset_1_reserves"]},
                            b'asset_2_reserves': {b'at': 2, b'ui': outputs["asset_2_reserves"]},
                            b'issued_pool_tokens': {b'at': 2, b'ui': outputs["issued_pool_token_amount"]}
                        }
                    )

    def test_subsequent_add_liqiudity(self):
        test_cases = [
            dict(
                msg="Remainder is NOT 0, expected asset amount rounded up.",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_250_000,
                    issued_pool_token_amount=1_118_033,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=10_000,
                    asset_2_added_liquidity_amount=15_000,
                ),
                outputs=dict(
                    asset_1_change_amount=None,
                    asset_2_change_amount=2_500,
                    pool_tokens_out_amount=11_180,
                )
            ),
            dict(
                msg="Remainder is 0, expected asset is NOT rounded up.",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=11_000,
                    asset_2_added_liquidity_amount=10_000,
                ),
                outputs=dict(
                    asset_1_change_amount=1_000,
                    asset_2_change_amount=None,
                    pool_tokens_out_amount=10_000
                )
            ),
            dict(
                msg="The changes (asset 1 and asset 2) are 0.",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=10_000,
                    asset_2_added_liquidity_amount=10_000,
                ),
                outputs=dict(
                    asset_1_change_amount=None,
                    asset_2_change_amount=0,
                    pool_tokens_out_amount=10_000
                )
            ),
            dict(
                msg="Test overflow by adding high liquidity to low liquidity pool.",
                initials=dict(
                    asset_1_reserves=LOCKED_POOL_TOKENS + 1,
                    asset_2_reserves=LOCKED_POOL_TOKENS + 1,
                    issued_pool_token_amount=LOCKED_POOL_TOKENS + 1,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=MAX_ASSET_AMOUNT - (LOCKED_POOL_TOKENS + 1),
                    asset_2_added_liquidity_amount=MAX_ASSET_AMOUNT - (LOCKED_POOL_TOKENS + 1),
                ),
                outputs=dict(
                    asset_1_change_amount=None,
                    asset_2_change_amount=0,
                    pool_tokens_out_amount=MAX_ASSET_AMOUNT - (LOCKED_POOL_TOKENS + 1)
                )
            ),
            dict(
                msg="Test overflow by adding high liquidity to high liquidity pool.",
                initials=dict(
                    asset_1_reserves=MAX_ASSET_AMOUNT // 2,
                    asset_2_reserves=MAX_ASSET_AMOUNT // 2,
                    issued_pool_token_amount=MAX_ASSET_AMOUNT // 2,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=MAX_ASSET_AMOUNT // 2 + 1,
                    asset_2_added_liquidity_amount=MAX_ASSET_AMOUNT // 2,
                ),
                outputs=dict(
                    asset_1_change_amount=1,
                    asset_2_change_amount=None,
                    pool_tokens_out_amount=MAX_ASSET_AMOUNT // 2
                )
            ),
            dict(
                msg="One of the added asset amount is 0. The pool token out is 0.",
                initials=dict(
                    asset_1_reserves=10_000,
                    asset_2_reserves=10_000,
                    issued_pool_token_amount=10_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=0,
                    asset_2_added_liquidity_amount=1,
                ),
                exception=dict(
                    source_line="assert(pool_tokens_out)",
                )
            ),
            dict(
                msg="Added asset 1 and asset 2 amounts are 0. The pool token out is 0.",
                initials=dict(
                    asset_1_reserves=10_000,
                    asset_2_reserves=10_000,
                    issued_pool_token_amount=10_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=0,
                    asset_2_added_liquidity_amount=0,
                ),
                exception=dict(
                    source_line="assert(pool_tokens_out)",
                )
            ),
            dict(
                msg="Added liquidiy is too small for the pool. The pool token out is 0.",
                initials=dict(
                    asset_1_reserves=10 ** 15,
                    asset_2_reserves=10 ** 3,
                    issued_pool_token_amount=10 ** 9,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=1,
                    asset_2_added_liquidity_amount=1,
                ),
                exception=dict(
                    source_line="assert(pool_tokens_out)",
                )
            ),
        ]

        for test_case in test_cases:
            with self.subTest(**test_case):
                self.reset_ledger()
                initials = test_case["initials"]
                inputs = test_case["inputs"]

                self.set_initial_pool_liquidity(asset_1_reserves=initials["asset_1_reserves"], asset_2_reserves=initials["asset_2_reserves"])
                self.assertEqual(initials["issued_pool_token_amount"], self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

                txn_group = self.get_add_liquidity_transactions(asset_1_amount=inputs["asset_1_added_liquidity_amount"], asset_2_amount=inputs["asset_2_added_liquidity_amount"], app_call_fee=3_000)
                txn_group = transaction.assign_group_id(txn_group)
                stxns = self.sign_txns(txn_group)

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        block = self.ledger.eval_transactions(stxns)

                    self.assertEqual(e.exception.source['line'], exception.get("source_line"))

                else:
                    outputs = test_case["outputs"]
                    assert outputs["asset_1_change_amount"] is None or outputs["asset_2_change_amount"] is None

                    self.assertEqual(
                        outputs["pool_tokens_out_amount"],
                        int(
                            min(
                                int(Decimal(inputs["asset_1_added_liquidity_amount"]) * Decimal(initials["issued_pool_token_amount"]) / Decimal(initials["asset_1_reserves"])),
                                int(Decimal(inputs["asset_2_added_liquidity_amount"]) * Decimal(initials["issued_pool_token_amount"]) / Decimal(initials["asset_2_reserves"]))
                            )
                        )
                    )

                    block = self.ledger.eval_transactions(stxns)
                    block_txns = block[b'txns']

                    # outer transactions
                    self.assertEqual(len(block_txns), 3)

                    # outer transactions - [0]
                    txn = block_txns[0]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'aamt': inputs["asset_1_added_liquidity_amount"],
                            b'arcv': decode_address(self.pool_address),
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'axfer',
                            b'xaid': self.asset_1_id
                        }
                    )

                    # outer transactions - [1]
                    txn = block_txns[1]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'aamt': inputs["asset_2_added_liquidity_amount"],
                            b'arcv': decode_address(self.pool_address),
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'axfer',
                            b'xaid': self.asset_2_id
                        }
                    )

                    # outer transactions - [2]
                    txn = block_txns[2]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'apaa': [b'add_liquidity'],
                            b'apas': [self.asset_1_id, self.asset_2_id, self.pool_token_asset_id],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee * 3,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'appl'
                        }
                    )

                    # inner transactions
                    inner_transactions = txn[b'dt'][b'itx']
                    self.assertEqual(len(inner_transactions), 2)

                    # inner transactions[0]
                    if outputs["asset_1_change_amount"] is not None:
                        self.assertDictEqual(
                            inner_transactions[0][b'txn'],
                            {
                                **({b'aamt': outputs["asset_1_change_amount"]} if outputs["asset_1_change_amount"] else {}),
                                b'fv': self.sp.first,
                                b'lv': self.sp.last,
                                b'arcv': decode_address(addr),
                                b'snd': decode_address(self.pool_address),
                                b'type': b'axfer',
                                b'xaid': self.asset_1_id
                            }
                        )
                    elif outputs["asset_2_change_amount"] is not None:
                        self.assertDictEqual(
                            inner_transactions[0][b'txn'],
                            {
                                **({b'aamt': outputs["asset_2_change_amount"]} if outputs["asset_2_change_amount"] else {}),
                                b'fv': self.sp.first,
                                b'lv': self.sp.last,
                                b'arcv': decode_address(addr),
                                b'snd': decode_address(self.pool_address),
                                b'type': b'axfer',
                                b'xaid': self.asset_2_id
                            }
                        )
                    else:
                        assert False

                    # inner transactions[1]
                    self.assertDictEqual(
                        inner_transactions[1][b'txn'],
                        {
                            b'aamt': outputs["pool_tokens_out_amount"],
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'arcv': decode_address(addr),
                            b'snd': decode_address(self.pool_address),
                            b'type': b'axfer',
                            b'xaid': self.pool_token_asset_id
                        }
                    )

                    # local state delta
                    pool_local_state_delta = txn[b'dt'][b'ld'][1]
                    self.assertDictEqual(
                        pool_local_state_delta,
                        {
                            b'asset_1_reserves': {b'at': 2, b'ui': initials["asset_1_reserves"] + inputs["asset_1_added_liquidity_amount"] - (outputs["asset_1_change_amount"] or 0)},
                            b'asset_2_reserves': {b'at': 2, b'ui': initials["asset_2_reserves"] + inputs["asset_2_added_liquidity_amount"] - (outputs["asset_2_change_amount"] or 0)},
                            b'issued_pool_tokens': {b'at': 2, b'ui': initials["issued_pool_token_amount"] + outputs["pool_tokens_out_amount"]}
                        }
                    )

    def test_fail_given_account_is_not_a_pool(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[2].accounts = [addr]
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'int asset_1_id = app_local_get(1, "asset_1_id")')

    def test_pass_composability(self):
        pass

    def test_fail_wrong_asset_transfer_order(self):
        pass


class TestAddLiquidityAlgoPair(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = ALGO_ASSET_ID
        cls.fee_tier = 3

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 2_000_000)
        self.ledger.set_account_balance(addr, 1_000_000, asset_id=self.asset_1_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, ALGO_ASSET_ID)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.opt_in_asset(addr, self.pool_token_asset_id)

    def test_pass_initial_add_liqiudity(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000
        issued_pool_token_amount = 12247    # int(sqrt(10_000 * 15_000))
        pool_tokens_out_amount = issued_pool_token_amount - LOCKED_POOL_TOKENS

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 3)

        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'aamt': asset_1_added_liquidity_amount,
                b'arcv': decode_address(self.pool_address),
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(addr),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            }
        )

        # outer transactions - [1]
        txn = block_txns[1]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'amt': asset_2_added_liquidity_amount,
                b'rcv': decode_address(self.pool_address),
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(addr),
                b'type': b'pay',
            }
        )

        # outer transactions - [2]
        txn = block_txns[2]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_liquidity'],
                b'apas': [self.asset_1_id, self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 2,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(addr),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions[0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': pool_tokens_out_amount,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'arcv': decode_address(addr),
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.pool_token_asset_id
            }
        )

        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'asset_1_reserves': {b'at': 2, b'ui': asset_1_added_liquidity_amount},
                b'asset_2_reserves': {b'at': 2, b'ui': asset_2_added_liquidity_amount},
                b'issued_pool_tokens': {b'at': 2, b'ui': issued_pool_token_amount}
            }
        )


class TestRemoveLiquidity(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.fee_tier = 3

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.set_account_balance(addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.opt_in_asset(addr, self.pool_token_asset_id)

    def setUp(self):
        self.reset_ledger()

    def test_remove_liquidity(self):
        test_cases = [
            dict(
                msg="Test basic remove liquidity.",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                ),
                inputs=dict(
                    removed_pool_token_amount=5_000,
                ),
                outputs=dict(
                    asset_1_out=5_000,
                    asset_2_out=5_000,
                    local_state_delta={
                        b'asset_1_reserves': {b'at': 2, b'ui': 1_000_000 - 5_000},
                        b'asset_2_reserves': {b'at': 2, b'ui': 1_000_000 - 5_000},
                        b'issued_pool_tokens': {b'at': 2, b'ui': 1_000_000 - 5_000},
                    }
                )
            ),
            dict(
                msg="Test removing 0 pool token. It should fail beacaue asset out amounts are 0.",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                ),
                inputs=dict(
                    removed_pool_token_amount=0,
                ),
                exception=dict(
                    source_line='assert(asset_1_out && asset_2_out)'
                )
            ),
            dict(
                msg="One of the asset out is 0 and asset out amounts are rounded down.",
                initials=dict(
                    asset_1_reserves=100_000_000,
                    asset_2_reserves=1,
                    issued_pool_token_amount=10_000,
                ),
                inputs=dict(
                    removed_pool_token_amount=500,
                ),
                exception=dict(
                    source_line='assert(asset_1_out && asset_2_out)'
                )
            ),
            dict(
                msg="Remove mistakenly added NFT (Remove all circulating pool tokens).",
                initials=dict(
                    asset_1_reserves=100_000_000,
                    asset_2_reserves=1,
                    issued_pool_token_amount=10_000,
                ),
                inputs=dict(
                    removed_pool_token_amount=10_000 - LOCKED_POOL_TOKENS,
                ),
                outputs=dict(
                    asset_1_out=100_000_000,
                    asset_2_out=1,
                    local_state_delta={
                        b'asset_1_reserves': {b'at': 2},
                        b'asset_2_reserves': {b'at': 2},
                        b'issued_pool_tokens': {b'at': 2},
                    }
                )
            )
        ]

        for test_case in test_cases:
            with self.subTest(**test_case):
                initials = test_case["initials"]
                inputs = test_case["inputs"]

                self.reset_ledger()
                self.set_initial_pool_liquidity(asset_1_reserves=initials["asset_1_reserves"], asset_2_reserves=initials["asset_2_reserves"], liquidity_provider_address=addr)
                self.assertEqual(initials["issued_pool_token_amount"], self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

                txn_group = self.get_remove_liquidity_transactions(liquidity_asset_amount=inputs["removed_pool_token_amount"], app_call_fee=3_000)
                txn_group = transaction.assign_group_id(txn_group)
                stxns = self.sign_txns(txn_group)

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        block = self.ledger.eval_transactions(stxns)

                    self.assertEqual(e.exception.source['line'], exception.get("source_line"))

                else:
                    outputs = test_case["outputs"]

                    block = self.ledger.eval_transactions(stxns)
                    block_txns = block[b'txns']

                    # outer transactions
                    self.assertEqual(len(block_txns), 2)

                    # outer transactions [0]
                    txn = block_txns[0]
                    self.assertEqual(
                        txn[b'txn'],
                        {
                            b'aamt': inputs["removed_pool_token_amount"],
                            b'arcv': decode_address(self.pool_address),
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'axfer',
                            b'xaid': self.pool_token_asset_id
                        }
                    )

                    # outer transactions [1]
                    txn = block_txns[1]
                    self.assertEqual(
                        txn[b'txn'],
                        {
                            b'apaa': [b'remove_liquidity'],
                            b'apas': [self.asset_1_id, self.asset_2_id],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee * 3,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(addr),
                            b'type': b'appl'
                        }
                    )

                    # inner transactions
                    inner_transactions = txn[b'dt'][b'itx']
                    self.assertEqual(len(inner_transactions), 2)

                    # inner transactions - [0]
                    self.assertDictEqual(
                        inner_transactions[0][b'txn'],
                        {
                            b'aamt': outputs["asset_1_out"],
                            b'arcv': decode_address(addr),
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'snd': decode_address(self.pool_address),
                            b'type': b'axfer',
                            b'xaid': self.asset_1_id
                        }
                    )

                    # inner transactions - [1]
                    self.assertDictEqual(
                        inner_transactions[1][b'txn'],
                        {
                            b'aamt': outputs["asset_2_out"],
                            b'arcv': decode_address(addr),
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'snd': decode_address(self.pool_address),
                            b'type': b'axfer',
                            b'xaid': self.asset_2_id
                        }
                    )

                    # local state delta
                    pool_local_state_delta = txn[b'dt'][b'ld'][1]
                    self.assertDictEqual(pool_local_state_delta, outputs["local_state_delta"])


class TestSwap(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.fee_tier = 3

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.set_account_balance(addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.ledger.set_account_balance(self.pool_address, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address, APPLICATION_ADDRESS)

    def test_fixed_input_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state={
                b'asset_1_id': self.asset_1_id,
                b'asset_2_id': self.asset_2_id,
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'poolers_fee_share': POOLERS_FEE_SHARE,
                b'protocol_fee_share': PROTOCOL_FEE_SHARE,
            }
        )

        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9000, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 9872)
        self.assertEqual(itxn0[b'arcv'], decode_address(addr))
        self.assertEqual(itxn0[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

    def test_fixed_output_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': self.asset_1_id,
            b'asset_2_id': self.asset_2_id,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': POOLERS_FEE_SHARE,
            b'protocol_fee_share': PROTOCOL_FEE_SHARE,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9872, "fixed-output"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 3000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 1)

        # Check details of output inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 9872)
        self.assertEqual(itxn0[b'arcv'], decode_address(addr))
        self.assertEqual(itxn0[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

    def test_fixed_output_with_change_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': self.asset_1_id,
            b'asset_2_id': self.asset_2_id,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': POOLERS_FEE_SHARE,
            b'protocol_fee_share': PROTOCOL_FEE_SHARE,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_100,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9872, "fixed-output"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 3000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 2)

        # Check details of input change inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 100)
        self.assertEqual(itxn0[b'arcv'], decode_address(addr))
        self.assertEqual(itxn0[b'xaid'], self.asset_1_id)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

        # Check details of output inner transaction
        itxn1 = txns[1][b'dt'][b'itx'][1][b'txn']
        self.assertEqual(itxn1[b'aamt'], 9872)
        self.assertEqual(itxn1[b'arcv'], decode_address(addr))
        self.assertEqual(itxn1[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn1[b'snd'], decode_address(self.pool_address))

    def test_fail_insufficient_fee(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': self.asset_1_id,
            b'asset_2_id': self.asset_2_id,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': POOLERS_FEE_SHARE,
            b'protocol_fee_share': PROTOCOL_FEE_SHARE,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9000, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 1000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('fee too small', e.exception.error)

    def test_fail_wrong_asset_in(self):
        self.ledger.set_account_balance(addr, 1_000_000, asset_id=0)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': self.asset_1_id,
            b'asset_2_id': self.asset_2_id,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': POOLERS_FEE_SHARE,
            b'protocol_fee_share': PROTOCOL_FEE_SHARE,
        })
        txn_group = [
            transaction.PaymentTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9000, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('assert failed', e.exception.error)

    def test_fail_wrong_asset_out_1(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': self.asset_1_id,
            b'asset_2_id': self.asset_2_id,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': POOLERS_FEE_SHARE,
            b'protocol_fee_share': PROTOCOL_FEE_SHARE,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, 0, 9000, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)

    def test_fail_wrong_asset_out_2(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': self.asset_1_id,
            b'asset_2_id': self.asset_2_id,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': POOLERS_FEE_SHARE,
            b'protocol_fee_share': PROTOCOL_FEE_SHARE,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_1_id, 9000, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)


if __name__ == '__main__':
    unittest.main()
