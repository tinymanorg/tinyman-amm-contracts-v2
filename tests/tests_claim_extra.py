from unittest.mock import ANY

from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase


class TestClaimExtra(BaseTestCase):
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

        algo_extra = 20_000
        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(algo_extra, 0, receiver=self.pool_address)
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=4_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'apat': [decode_address(self.pool_address), decode_address(fee_collector)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 3)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': algo_extra,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
            },
        )

        # inner transactions - [1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
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

        # inner transactions - [2]
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
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

    def test_pass_only_one_of_the_asset_has_extra(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_extra = 0
        asset_2_extra = 5_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=4_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)

        txn = block_txns[0]
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 3)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
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
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [2]
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
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

    def test_pass_only_algo_has_extra(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        algo_extra = 5_000
        self.ledger.move(algo_extra, 0, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=4_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)

        txn = block_txns[0]
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 3)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': algo_extra,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
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
                b'xaid': self.asset_1_id
            },
        )

        # inner transactions - [2]
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
            {
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            },
        )

    def test_fail_there_is_no_extra(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        asset_1_extra = 0
        asset_2_extra = 0
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=4_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert((asset_1_amount || asset_2_amount) || algo_amount)')

    def test_fail_fee_collector_did_not_opt_in(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')


class TestClaimExtraAlgoPair(BaseTestCase):
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

        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'apat': [decode_address(self.pool_address), decode_address(fee_collector)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
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
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)

        asset_1_extra = 5_000
        asset_2_extra = 0
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, fee_collector=fee_collector, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'apat': [decode_address(self.pool_address), decode_address(fee_collector)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
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
