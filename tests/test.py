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

user_addr = 'RTR6MP4VKLZRBLKTNWR4PDH5QGMQYVDRQ6OSBEYR6OJLK7W2YKY2HFGKLE'
user_sk = 'vJB2vFVww2xs7fvfZcr8LQTWkGO5MEwS+jwfRfzcoZeM4+Y/lVLzEK1TbaPHjP2BmQxUcYedIJMR85K1ftrCsQ=='
app_creator_sk, app_creator_address = generate_account()

logicsig = TealishProgram('contracts/pool_template.tl')
approval_program = TealishProgram('contracts/amm_approval.tl')
clear_state_program = TealishProgram('contracts/amm_clear_state.tl')

METHOD_BOOTSTRAP = "bootstrap"
METHOD_ADD_LIQUIDITY = "add_liquidity"
METHOD_REMOVE_LIQUIDITY = "remove_liquidity"
METHOD_SWAP = "swap"
METHOD_CLAIM_FEES = "claim_fees"
METHOD_CLAIM_EXTRA = "claim_extra"
METHOD_SET_FEE = "set_fee"
METHOD_SET_FEE_COLLECTOR = "set_fee_collector"
METHOD_SET_FEE_SETTER = "set_fee_setter"
METHOD_SET_FEE_MANAGER = "set_fee_manager"

POOLERS_FEE_SHARE = 25
PROTOCOL_FEE_SHARE = 5
LOCKED_POOL_TOKENS = 1_000

MAX_ASSET_AMOUNT = 18446744073709551615
POOL_TOKEN_TOTAL_SUPPLY = MAX_ASSET_AMOUNT
ALGO_ASSET_ID = 0
APPLICATION_ID = 1
APPLICATION_ADDRESS = get_application_address(APPLICATION_ID)
print('App Address:', APPLICATION_ADDRESS)

PROXY_APP_ID = 10


def get_pool_logicsig_bytecode(asset_1_id, asset_2_id):
    # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
    template = b'\x06\x80\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
    program = bytearray(logicsig.bytecode)

    # Algo SDK doesn't support teal version 6 at the moment
    program[0:1] = (6).to_bytes(1, "big")
    assert program == bytearray(template)

    program[3:11] = (APPLICATION_ID).to_bytes(8, 'big')
    program[11:19] = asset_1_id.to_bytes(8, 'big')
    program[19:27] = asset_2_id.to_bytes(8, 'big')
    return transaction.LogicSigAccount(program)


# lsig = get_pool_logicsig_bytecode(5, 2)
# pool_address = lsig.address()
# print('Pool Address:', pool_address)

class BaseTestCase(unittest.TestCase):
    maxDiff = None

    def create_amm_app(self):
        if app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(app_creator_address, 1_000_000)

        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program, creator=app_creator_address)
        self.ledger.set_global_state(
            APPLICATION_ID,
            {
                b'fee_collector': decode_address(app_creator_address),
                b'fee_manager': decode_address(app_creator_address),
                b'fee_setter': decode_address(app_creator_address),
            }
        )

    def bootstrap_pool(self):
        asset_2_id = getattr(self, "asset_2_id", ALGO_ASSET_ID)
        minimum_balance = 500_000 if asset_2_id else 400_000

        # Set Algo balance
        self.ledger.set_account_balance(self.pool_address, minimum_balance)

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
                b'pool_token_asset_id': self.pool_token_asset_id,
                b'poolers_fee_share': POOLERS_FEE_SHARE,
                b'protocol_fee_share': PROTOCOL_FEE_SHARE,
            }
        )

    def set_initial_pool_liquidity(self, asset_1_reserves, asset_2_reserves, liquidity_provider_address=None):
        issued_pool_token_amount = int(Decimal.sqrt(Decimal(asset_1_reserves) * Decimal(asset_2_reserves)))
        pool_token_out_amount = issued_pool_token_amount - LOCKED_POOL_TOKENS
        assert pool_token_out_amount > 0

        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': asset_1_reserves,
                b'asset_2_reserves': asset_2_reserves,
                b'issued_pool_tokens': issued_pool_token_amount
            }
        )

        self.ledger.move(sender=liquidity_provider_address, receiver=self.pool_address, amount=asset_1_reserves, asset_id=self.asset_1_id)
        self.ledger.move(sender=liquidity_provider_address, receiver=self.pool_address, amount=asset_2_reserves, asset_id=self.asset_2_id)
        self.ledger.move(sender=self.pool_address, receiver=liquidity_provider_address, amount=pool_token_out_amount, asset_id=self.pool_token_asset_id)

    def set_pool_protocol_fees(self, protocol_fees_asset_1, protocol_fees_asset_2):
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'protocol_fees_asset_1': protocol_fees_asset_1,
                b'protocol_fees_asset_2': protocol_fees_asset_2,
            }
        )

        self.ledger.move(receiver=self.pool_address, amount=protocol_fees_asset_1, asset_id=self.asset_1_id)
        self.ledger.move(receiver=self.pool_address, amount=protocol_fees_asset_2, asset_id=self.asset_1_id)

    def get_add_liquidity_transactions(self, asset_1_amount, asset_2_amount, app_call_fee=None):
        txn_group = [
            transaction.AssetTransferTxn(
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_amount,
            ),
            transaction.AssetTransferTxn(
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=asset_2_amount,
            ) if self.asset_2_id else transaction.PaymentTxn(
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=asset_2_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.pool_token_asset_id,
                amt=liquidity_asset_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_REMOVE_LIQUIDITY],
                foreign_assets=[self.asset_1_id, self.asset_2_id] if self.asset_2_id else [self.asset_1_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_claim_fee_transactions(self, fee_collector, app_call_fee=None):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=fee_collector,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_CLAIM_FEES],
                foreign_assets=[self.asset_1_id, self.asset_2_id] if self.asset_2_id else [self.asset_1_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_claim_extra_transactions(self, fee_collector, app_call_fee=None):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=fee_collector,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_CLAIM_EXTRA],
                foreign_assets=[self.asset_1_id, self.asset_2_id] if self.asset_2_id else [self.asset_1_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = app_call_fee or self.sp.fee
        return txn_group

    @classmethod
    def sign_txns(cls, txns, secret_key=user_sk):
        return [txn.sign(secret_key)for txn in txns]


class TestCreateApp(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.set_account_balance(app_creator_address, 1_000_000)

    def test_create_app(self):
        extra_pages = 1
        txn = transaction.ApplicationCreateTxn(
            sender=app_creator_address,
            sp=self.sp,
            on_complete=transaction.OnComplete.NoOpOC,
            approval_program=approval_program.bytecode,
            clear_program=clear_state_program.bytecode,
            global_schema=transaction.StateSchema(num_uints=1, num_byte_slices=3),
            local_schema=transaction.StateSchema(num_uints=11, num_byte_slices=0),
            extra_pages=extra_pages,
        )
        stxn = txn.sign(app_creator_sk)

        block = self.ledger.eval_transactions(transactions=[stxn])
        block_txns = block[b'txns']

        self.assertAlmostEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertTrue(txn[b'apid'] > 0)
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apap': approval_program.bytecode,
                b'apep': extra_pages,
                b'apgs': ANY,
                b'apls': ANY,
                b'apsu': clear_state_program.bytecode,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(app_creator_address),
                b'type': b'appl'
            }
        )

        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_collector': {b'at': 1, b'bs': decode_address(app_creator_address)},
                b'fee_manager': {b'at': 1, b'bs': decode_address(app_creator_address)},
                b'fee_setter': {b'at': 1, b'bs': decode_address(app_creator_address)},
            }
        )


class TestBootstrap(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.minimum_fee = 7000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USD"))
        self.ledger.create_asset(self.asset_2_id, params=dict(unit_name="BTC"))
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_2_id)

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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
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
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), self.asset_2_id.to_bytes(8, "big")],
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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(hash == pool_address)')

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
                    app_args=[METHOD_BOOTSTRAP, self.asset_2_id, self.asset_1_id],
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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
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
                    app_args=["invalid", self.asset_1_id, self.asset_2_id],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], f'assert(Txn.ApplicationArgs[0] == "{METHOD_BOOTSTRAP}")')

    def test_fail_bad_asset_1_total(self):
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="NFT", total=100))
        self.ledger.create_asset(self.asset_2_id, params=dict(unit_name="BTC"))
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = self.minimum_fee - 1

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(exists && (asset_total > ASSET_MIN_TOTAL_SUPPLY))')

    def test_fail_bad_asset_2_total(self):
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USDC"))
        self.ledger.create_asset(self.asset_2_id, params=dict(unit_name="NFT", total=1))
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = self.minimum_fee - 1

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(exists && (asset_total > ASSET_MIN_TOTAL_SUPPLY))')


class TestBootstrapAlgoPair(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.minimum_fee = 6000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.asset_2_id = ALGO_ASSET_ID
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USD"))
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_1_id)

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
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, ALGO_ASSET_ID],
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
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), ALGO_ASSET_ID.to_bytes(8, "big")],
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

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

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
                        self.ledger.eval_transactions(stxns)

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
                            b'snd': decode_address(user_addr),
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
                            b'snd': decode_address(user_addr),
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
                            b'snd': decode_address(user_addr),
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
                            b'arcv': decode_address(user_addr),
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
                        self.ledger.eval_transactions(stxns)

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
                            b'snd': decode_address(user_addr),
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
                            b'snd': decode_address(user_addr),
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
                            b'snd': decode_address(user_addr),
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
                                b'arcv': decode_address(user_addr),
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
                                b'arcv': decode_address(user_addr),
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
                            b'arcv': decode_address(user_addr),
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
        txn_group[2].accounts = [user_addr]
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

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 2_000_000)
        self.ledger.set_account_balance(user_addr, 1_000_000, asset_id=self.asset_1_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, ALGO_ASSET_ID)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

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
                b'snd': decode_address(user_addr),
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
                b'snd': decode_address(user_addr),
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
                b'snd': decode_address(user_addr),
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
                b'arcv': decode_address(user_addr),
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

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

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
                    source_line='assert(asset_1_amount && asset_2_amount)'
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
                    source_line='assert(asset_1_amount && asset_2_amount)'
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
                self.set_initial_pool_liquidity(asset_1_reserves=initials["asset_1_reserves"], asset_2_reserves=initials["asset_2_reserves"], liquidity_provider_address=user_addr)
                self.assertEqual(initials["issued_pool_token_amount"], self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

                txn_group = self.get_remove_liquidity_transactions(liquidity_asset_amount=inputs["removed_pool_token_amount"], app_call_fee=3_000)
                txn_group = transaction.assign_group_id(txn_group)
                stxns = self.sign_txns(txn_group)

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        self.ledger.eval_transactions(stxns)

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
                            b'snd': decode_address(user_addr),
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
                            b'snd': decode_address(user_addr),
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
                            b'arcv': decode_address(user_addr),
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
                            b'arcv': decode_address(user_addr),
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

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_2_id)

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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 9872)
        self.assertEqual(itxn0[b'arcv'], decode_address(user_addr))
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 1)

        # Check details of output inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 9872)
        self.assertEqual(itxn0[b'arcv'], decode_address(user_addr))
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_100,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 2)

        # Check details of input change inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 100)
        self.assertEqual(itxn0[b'arcv'], decode_address(user_addr))
        self.assertEqual(itxn0[b'xaid'], self.asset_1_id)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

        # Check details of output inner transaction
        itxn1 = txns[1][b'dt'][b'itx'][1][b'txn']
        self.assertEqual(itxn1[b'aamt'], 9872)
        self.assertEqual(itxn1[b'arcv'], decode_address(user_addr))
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('fee too small', e.exception.error)

    def test_fail_wrong_asset_in(self):
        self.ledger.set_account_balance(user_addr, 1_000_000, asset_id=0)
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
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
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
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
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)


class TestClaimFees(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)
        self.set_initial_pool_liquidity(asset_1_reserves=1_000_000, asset_2_reserves=1_000_000, liquidity_provider_address=user_addr)

    def test_pass(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 10_000
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'claim_fees'],
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(fee_collector),
                b'type': b'appl'
            }
        )

        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': asset_1_fee_amount,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': asset_2_fee_amount,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            },
        )

        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'protocol_fees_asset_1': {b'at': 2},   # -> 0
                b'protocol_fees_asset_2': {b'at': 2}    # -> 0
            }
        )

    def test_fail_sender_is_not_fee_collector(self):
        asset_1_fee_amount = 0
        asset_2_fee_amount = 0
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(fee_collector=user_addr, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_collector"))')

    def test_pass_only_one_of_the_asset_has_fee(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 0
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)

        txn = block_txns[0]
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': asset_1_fee_amount,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            },
        )

        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'protocol_fees_asset_1': {b'at': 2},   # -> 0
            }
        )

    def test_fail_there_is_no_fee(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_fee_amount = 0
        asset_2_fee_amount = 0
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(protocol_fees_asset_1 || protocol_fees_asset_2)')

    def test_fail_fee_collector_did_not_opt_in(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 10_000
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')


class TestClaimFeesAlgoPair(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = ALGO_ASSET_ID

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 100_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)
        self.set_initial_pool_liquidity(asset_1_reserves=1_000_000, asset_2_reserves=1_000_000, liquidity_provider_address=user_addr)

    def test_pass(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 10_000
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'claim_fees'],
                b'apas': [self.asset_1_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(fee_collector),
                b'type': b'appl'
            }
        )

        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': asset_1_fee_amount,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'amt': asset_2_fee_amount,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
            },
        )

        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'protocol_fees_asset_1': {b'at': 2},   # -> 0
                b'protocol_fees_asset_2': {b'at': 2}    # -> 0
            }
        )


class TestClaimExtra(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)
        self.set_initial_pool_liquidity(asset_1_reserves=1_000_000, asset_2_reserves=1_000_000, liquidity_provider_address=user_addr)

    def test_pass(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'claim_extra'],
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(fee_collector),
                b'type': b'appl'
            }
        )

        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': asset_1_extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': asset_2_extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            },
        )

    def test_fail_sender_is_not_fee_collector(self):
        asset_1_extra = 0
        asset_2_extra = 0
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=user_addr, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_collector"))')

    def test_pass_only_one_of_the_asset_has_extra(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_extra = 0
        asset_2_extra = 5_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)

        txn = block_txns[0]
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': asset_2_extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            },
        )

    def test_fail_there_is_no_extra(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_extra = 0
        asset_2_extra = 0
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_amount || asset_2_amount)')

    def test_fail_fee_collector_did_not_opt_in(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')


class TestClaimExtraAlgoPair(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = ALGO_ASSET_ID

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 100_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)
        self.set_initial_pool_liquidity(asset_1_reserves=1_000_000, asset_2_reserves=1_000_000, liquidity_provider_address=user_addr)

    def test_pass(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)

        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'claim_extra'],
                b'apas': [self.asset_1_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(fee_collector),
                b'type': b'appl'
            }
        )

        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': asset_1_extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'amt': asset_2_extra,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
            },
        )

    def test_pass_there_is_no_algo_extra(self):
        fee_collector = app_creator_address
        fee_collector_sk = app_creator_sk
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)

        asset_1_extra = 5_000
        asset_2_extra = 0
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, fee_collector_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'claim_extra'],
                b'apas': [self.asset_1_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(fee_collector),
                b'type': b'appl'
            }
        )

        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': asset_1_extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
            },
        )


class TestSetFeeManager(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

    def test_pass(self):
        fee_manager_1_sk, fee_manager_1 = generate_account()
        _, fee_manager_2 = generate_account()
        self.ledger.set_account_balance(app_creator_address, 1_000_000)
        self.ledger.set_account_balance(fee_manager_1, 1_000_000)
        self.ledger.set_account_balance(fee_manager_2, 1_000_000)

        # Group is not required.
        # Creator sets fee_manager to fee_manager_1
        # fee_manager_1 sets fee_manager to fee_manager_2
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=app_creator_address,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_MANAGER],
                accounts=[fee_manager_1],
            ),
            transaction.ApplicationNoOpTxn(
                sender=fee_manager_1,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_MANAGER],
                accounts=[fee_manager_2],
            )
        ]
        stxns = [
            txns[0].sign(app_creator_sk),
            txns[1].sign(fee_manager_1_sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions[0]
        txn = block_txns[0]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_manager'],
                b'apat': [decode_address(fee_manager_1)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(app_creator_address),
                b'type': b'appl'
            }
        )
        # outer transactions[0] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_manager': {b'at': 1, b'bs': decode_address(fee_manager_1)}
            }
        )

        # outer transactions[1]
        txn = block_txns[1]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_manager'],
                b'apat': [decode_address(fee_manager_2)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager_1),
                b'type': b'appl'
            }
        )

        # outer transactions[1] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_manager': {b'at': 1, b'bs': decode_address(fee_manager_2)}
            }
        )

    def test_fail_sender_is_not_fee_manager(self):
        invalid_account_sk, invalid_account_address = generate_account()
        self.ledger.set_account_balance(app_creator_address, 1_000_000)
        self.ledger.set_account_balance(invalid_account_address, 1_000_000)

        stxn = transaction.ApplicationNoOpTxn(
            sender=invalid_account_address,
            sp=self.sp,
            index=APPLICATION_ID,
            app_args=[METHOD_SET_FEE_MANAGER],
            accounts=[invalid_account_address],
        ).sign(invalid_account_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions([stxn])
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_manager"))')


class TestSetFeeSetter(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

    def test_pass(self):
        fee_manager_sk, fee_manager = app_creator_sk, app_creator_address
        _, fee_setter_1 = generate_account()
        _, fee_setter_2 = generate_account()
        self.ledger.set_account_balance(fee_manager, 1_000_000)
        self.ledger.set_account_balance(fee_setter_1, 1_000_000)
        self.ledger.set_account_balance(fee_setter_2, 1_000_000)

        # Group is not required.
        # Creator sets fee_setter to fee_setter_1
        # fee_setter_1 sets fee_setter to fee_setter_2
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=fee_manager,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_SETTER],
                accounts=[fee_setter_1],
            ),
            transaction.ApplicationNoOpTxn(
                sender=fee_manager,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_SETTER],
                accounts=[fee_setter_2],
            )
        ]
        stxns = [
            txns[0].sign(fee_manager_sk),
            txns[1].sign(fee_manager_sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions[0]
        txn = block_txns[0]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_setter'],
                b'apat': [decode_address(fee_setter_1)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager),
                b'type': b'appl'
            }
        )
        # outer transactions[0] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_setter': {b'at': 1, b'bs': decode_address(fee_setter_1)}
            }
        )

        # outer transactions[1]
        txn = block_txns[1]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_setter'],
                b'apat': [decode_address(fee_setter_2)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager),
                b'type': b'appl'
            }
        )

        # outer transactions[1] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_setter': {b'at': 1, b'bs': decode_address(fee_setter_2)}
            }
        )

    def test_fail_sender_is_not_fee_setter(self):
        invalid_account_sk, invalid_account_address = generate_account()
        self.ledger.set_account_balance(app_creator_address, 1_000_000)
        self.ledger.set_account_balance(invalid_account_address, 1_000_000)

        stxn = transaction.ApplicationNoOpTxn(
            sender=invalid_account_address,
            sp=self.sp,
            index=APPLICATION_ID,
            app_args=[METHOD_SET_FEE_SETTER],
            accounts=[invalid_account_address],
        ).sign(invalid_account_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions([stxn])
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_manager"))')


class TestSetFeeCollector(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

    def test_pass(self):
        fee_manager_sk, fee_manager = app_creator_sk, app_creator_address
        _, fee_collector_1 = generate_account()
        _, fee_collector_2 = generate_account()
        self.ledger.set_account_balance(fee_manager, 1_000_000)
        self.ledger.set_account_balance(fee_collector_1, 1_000_000)
        self.ledger.set_account_balance(fee_collector_2, 1_000_000)

        # Group is not required.
        # Creator sets fee_collector to fee_collector_1
        # fee_collector_1 sets fee_collector to fee_collector_2
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=fee_manager,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_COLLECTOR],
                accounts=[fee_collector_1],
            ),
            transaction.ApplicationNoOpTxn(
                sender=fee_manager,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_COLLECTOR],
                accounts=[fee_collector_2],
            )
        ]
        stxns = [
            txns[0].sign(fee_manager_sk),
            txns[1].sign(fee_manager_sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions[0]
        txn = block_txns[0]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_collector'],
                b'apat': [decode_address(fee_collector_1)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager),
                b'type': b'appl'
            }
        )
        # outer transactions[0] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_collector': {b'at': 1, b'bs': decode_address(fee_collector_1)}
            }
        )

        # outer transactions[1]
        txn = block_txns[1]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_collector'],
                b'apat': [decode_address(fee_collector_2)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager),
                b'type': b'appl'
            }
        )

        # outer transactions[1] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_collector': {b'at': 1, b'bs': decode_address(fee_collector_2)}
            }
        )

    def test_fail_sender_is_not_fee_collector(self):
        invalid_account_sk, invalid_account_address = generate_account()
        self.ledger.set_account_balance(app_creator_address, 1_000_000)
        self.ledger.set_account_balance(invalid_account_address, 1_000_000)

        stxn = transaction.ApplicationNoOpTxn(
            sender=invalid_account_address,
            sp=self.sp,
            index=APPLICATION_ID,
            app_args=[METHOD_SET_FEE_COLLECTOR],
            accounts=[invalid_account_address],
        ).sign(invalid_account_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions([stxn])
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_manager"))')


class TestSetFee(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(user_addr, self.pool_token_asset_id)

    def setUp(self):
        self.reset_ledger()

    def test_set_fee(self):
        test_cases = [
            dict(
                msg="Test maks.",
                inputs=dict(
                    poolers_fee_share=50,
                    protocol_fee_share=10
                ),
            ),
            dict(
                msg="Test mins.",
                inputs=dict(
                    poolers_fee_share=0,
                    protocol_fee_share=0
                ),
            ),
            dict(
                msg="Protocol fee is 0.",
                inputs=dict(
                    poolers_fee_share=10,
                    protocol_fee_share=0
                ),
            ),
            dict(
                msg="Test invalid poolers share.",
                inputs=dict(
                    poolers_fee_share=51,
                    protocol_fee_share=10
                ),
                exception=dict(
                    source_line='assert(poolers_fee_share <= 50)',
                )
            ),
            dict(
                msg="Test invalid protocol share.",
                inputs=dict(
                    poolers_fee_share=50,
                    protocol_fee_share=11
                ),
                exception=dict(
                    source_line='assert(protocol_fee_share <= 10)',
                )
            ),
            dict(
                msg="Tes invalid ratio.",
                inputs=dict(
                    poolers_fee_share=14,
                    protocol_fee_share=3
                ),
                exception=dict(
                    source_line='assert(poolers_fee_share >= (protocol_fee_share * 5))',
                )
            ),
        ]

        for test_case in test_cases:
            with self.subTest(**test_case):
                self.reset_ledger()
                inputs = test_case["inputs"]

                stxns = [
                    transaction.ApplicationNoOpTxn(
                        sender=app_creator_address,
                        sp=self.sp,
                        index=APPLICATION_ID,
                        app_args=[METHOD_SET_FEE, inputs["poolers_fee_share"], inputs["protocol_fee_share"]],
                        accounts=[self.pool_address],
                    ).sign(app_creator_sk)
                ]

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        block = self.ledger.eval_transactions(stxns)

                    self.assertEqual(e.exception.source['line'], exception.get("source_line"))

                else:
                    block = self.ledger.eval_transactions(stxns)
                    block_txns = block[b'txns']

                    # outer transactions
                    self.assertEqual(len(block_txns), 1)

                    # outer transactions[0]
                    txn = block_txns[0]
                    # there is no inner transaction
                    self.assertIsNone(txn[b'dt'].get(b'itx'))
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'apaa': [b'set_fee', inputs["poolers_fee_share"].to_bytes(8, 'big'), inputs["protocol_fee_share"].to_bytes(8, 'big')],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'snd': decode_address(app_creator_address),
                            b'type': b'appl'
                        }
                    )

                    # outer transactions[0] - Pool State Delta
                    self.assertDictEqual(
                        txn[b'dt'][b'ld'],
                        {
                            1: {
                                b'poolers_fee_share': {b'at': 2, **({b'ui': inputs["poolers_fee_share"]} if inputs["poolers_fee_share"] else {})},
                                b'protocol_fee_share': {b'at': 2, **({b'ui': inputs["protocol_fee_share"]} if inputs["protocol_fee_share"] else {})}
                            }
                        }
                    )

    def test_sender(self):
        self.ledger.set_account_balance(app_creator_address, 1_000_000)

        # Sender is not fee setter (app creator default)
        new_account_sk, new_account_address = generate_account()
        self.ledger.set_account_balance(new_account_address, 1_000_000)
        stxns = [
            transaction.ApplicationNoOpTxn(
                sender=new_account_address,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE, 10, 2],
                accounts=[self.pool_address],
            ).sign(new_account_sk)
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_setter"))')

        self.ledger.update_global_state(app_id=APPLICATION_ID, state_delta={b"fee_setter": decode_address(new_account_address)})
        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)

        # outer transactions[0]
        txn = block_txns[0]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee', (10).to_bytes(8, 'big'), (2).to_bytes(8, 'big')],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(new_account_address),
                b'type': b'appl'
            }
        )

        # outer transactions[0] - Pool State Delta
        self.assertDictEqual(
            txn[b'dt'][b'ld'],
            {
                1: {
                    b'poolers_fee_share': {b'at': 2, b'ui': 10},
                    b'protocol_fee_share': {b'at': 2, b'ui': 2}
                }
            }
        )

proxy_approval_program = TealishProgram(tealish="""
    #pragma version 7

    const int TINYMAN_APP_ID = 1
    const int FEE_BASIS_POINTS = 100

    assert(Gtxn[0].AssetReceiver == Global.CurrentApplicationAddress)
    int swap_amount = (Gtxn[0].AssetAmount * (10000 - FEE_BASIS_POINTS)) / 10000
    int initial_output_balance
    _, initial_output_balance = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, Txn.Assets[1])
    inner_group:
        inner_txn:
            TypeEnum: Axfer
            Fee: 0
            AssetReceiver: Txn.Accounts[1]
            XferAsset: Gtxn[0].XferAsset
            AssetAmount: swap_amount
        end
        inner_txn:
            TypeEnum: Appl
            Fee: 0
            ApplicationID: TINYMAN_APP_ID
            ApplicationArgs[0]: "swap"
            ApplicationArgs[1]: Txn.ApplicationArgs[1]
            ApplicationArgs[2]: Txn.ApplicationArgs[2]
            ApplicationArgs[3]: Txn.ApplicationArgs[3]
            ApplicationArgs[4]: "fixed-input"
            Accounts[0]: Txn.Accounts[1]
            Assets[0]: Txn.Assets[0]
            Assets[1]: Txn.Assets[1]
        end
    end

    int new_output_balance
    _, new_output_balance = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, Txn.Assets[1])
    int output_amount = new_output_balance - initial_output_balance
    inner_txn:
        TypeEnum: Axfer
        Fee: 0
        AssetReceiver: Txn.Sender
        XferAsset: Txn.Assets[1]
        AssetAmount: output_amount
    end
    exit(1)
""")

PROXY_ADDRESS = get_application_address(PROXY_APP_ID)


class TestProxySwap(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_2_id)

        self.ledger.create_app(app_id=PROXY_APP_ID, approval_program=proxy_approval_program, creator=app_creator_address)
        self.ledger.set_account_balance(PROXY_ADDRESS, 1_000_000)
        self.ledger.set_account_balance(PROXY_ADDRESS, 0, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(PROXY_ADDRESS, 0, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.ledger.set_account_balance(self.pool_address, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address, APPLICATION_ADDRESS)

    def test_pass(self):
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
                sender=user_addr,
                sp=self.sp,
                receiver=PROXY_ADDRESS,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
                sp=self.sp,
                index=PROXY_APP_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9000],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                foreign_apps=[APPLICATION_ID],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 5000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk),
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        itxn = txns[1][b'dt'][b'itx'][-1][b'txn']
        self.assertEqual(itxn[b'aamt'], 9776)
        self.assertEqual(itxn[b'arcv'], decode_address(user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn[b'snd'], decode_address(PROXY_ADDRESS))

        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 100)

        # do the same swap again and watch the fees accumulate
        block = self.ledger.eval_transactions(stxns)
        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 200)



class TestGroupedSwap(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.asset_3_id = 7

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(user_addr, 1_000_000)
        self.ledger.set_account_balance(user_addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_2_id)
        self.ledger.set_account_balance(user_addr, 0, asset_id=self.asset_3_id)

        lsig1 = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        self.pool_address1 = lsig1.address()
        self.ledger.set_account_balance(self.pool_address1, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address1, APPLICATION_ADDRESS)

        lsig2 = get_pool_logicsig_bytecode(self.asset_2_id, self.asset_3_id)
        self.pool_address2 = lsig2.address()
        self.ledger.set_account_balance(self.pool_address2, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address2, APPLICATION_ADDRESS)

    def test_pass(self):
        self.ledger.set_account_balance(self.pool_address1, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address1, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_local_state(
            address=self.pool_address1,
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

        self.ledger.set_account_balance(self.pool_address2, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.set_account_balance(self.pool_address2, 1_000_000, asset_id=self.asset_3_id)
        self.ledger.set_local_state(
            address=self.pool_address2,
            app_id=APPLICATION_ID,
            state={
                b'asset_1_id': self.asset_2_id,
                b'asset_2_id': self.asset_3_id,
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'poolers_fee_share': POOLERS_FEE_SHARE,
                b'protocol_fee_share': PROTOCOL_FEE_SHARE,
            }
        )

        txn_group = [
            # Swap 1
            transaction.AssetTransferTxn(
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address1,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9872, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address1],
            ),

            # Swap 2
            transaction.AssetTransferTxn(
                sender=user_addr,
                sp=self.sp,
                receiver=self.pool_address2,
                index=self.asset_2_id,
                amt=9872,
            ),
            transaction.ApplicationNoOpTxn(
                sender=user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_2_id, self.asset_3_id, 9749, "fixed-input"],
                foreign_assets=[self.asset_2_id, self.asset_3_id],
                accounts=[self.pool_address2],
            )
        ]
        txn_group[1].fee = 5000
        txn_group[3].fee = 5000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(user_sk),
            txn_group[1].sign(user_sk),
            txn_group[2].sign(user_sk),
            txn_group[3].sign(user_sk),
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']

        itxn = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn[b'aamt'], 9872)
        self.assertEqual(itxn[b'arcv'], decode_address(user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn[b'snd'], decode_address(self.pool_address1))

        itxn = txns[3][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn[b'aamt'], 9749)
        self.assertEqual(itxn[b'arcv'], decode_address(user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_3_id)
        self.assertEqual(itxn[b'snd'], decode_address(self.pool_address2))

        self.assertEqual(self.ledger.get_account_balance(user_addr, self.asset_2_id)[0], 0)
        self.assertEqual(self.ledger.get_account_balance(user_addr, self.asset_3_id)[0], 9749)



if __name__ == '__main__':
    unittest.main()
