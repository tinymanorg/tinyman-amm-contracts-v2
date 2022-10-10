from unittest.mock import ANY

from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address

from .constants import *
from .core import BaseTestCase


class TestClaimFees(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(self.user_addr, 1_000_000)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        self.pool_address, self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, self.pool_token_asset_id)
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000, liquidity_provider_address=self.user_addr)

    def test_pass(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 10_000
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'apat': [decode_address(self.pool_address), decode_address(fee_collector)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.user_addr),
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
                b'asset_1_protocol_fees': {b'at': 2},   # -> 0
                b'asset_2_protocol_fees': {b'at': 2},   # -> 0
            }
        )

    def test_pass_only_one_of_the_asset_has_fee(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 0
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'asset_1_protocol_fees': {b'at': 2},   # -> 0
            }
        )

    def test_fail_there_is_no_fee(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_fee_amount = 0
        asset_2_fee_amount = 0
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_protocol_fees || asset_2_protocol_fees)')

    def test_fail_fee_collector_did_not_opt_in(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 10_000
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')


class TestClaimFeesAlgoPair(BaseTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()
        cls.asset_1_id = 5
        cls.asset_2_id = ALGO_ASSET_ID

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(self.user_addr, 100_000_000)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)

        self.pool_address, self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, self.pool_token_asset_id)
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000, liquidity_provider_address=self.user_addr)

    def test_pass(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)

        asset_1_fee_amount = 5_000
        asset_2_fee_amount = 10_000
        self.set_pool_protocol_fees(asset_1_fee_amount, asset_2_fee_amount)

        txn_group = self.get_claim_fee_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'apat': [decode_address(self.pool_address), decode_address(fee_collector)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.user_addr),
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
                b'asset_1_protocol_fees': {b'at': 2},   # -> 0
                b'asset_2_protocol_fees': {b'at': 2}    # -> 0
            }
        )
