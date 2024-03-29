from unittest import skip
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

    def test_claim_asset_1_from_pool(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_1_id)

        extra = 5_000
        self.ledger.move(extra, self.asset_1_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_1_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [self.asset_1_id],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            },
        )

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_1_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')

    def test_claim_asset_2_from_pool(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.asset_2_id)

        extra = 10_000
        self.ledger.move(extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_2_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [self.asset_2_id],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            },
        )

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_2_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')

    def test_claim_pool_token_asset_from_pool(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.pool_token_asset_id)

        extra = 15_000
        self.ledger.move(extra, self.pool_token_asset_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.pool_token_asset_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [self.pool_token_asset_id],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'aamt': extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.pool_token_asset_id
            },
        )

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.pool_token_asset_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')

    def test_claim_algo_from_pool(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        extra = 1_345
        self.ledger.move(extra, ALGO_ASSET_ID, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=ALGO_ASSET_ID, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [ALGO_ASSET_ID],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': extra,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
            },
        )

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.pool_token_asset_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')

    def test_fail_fee_collector_did_not_opt_in(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        asset_1_extra = 5_000
        asset_2_extra = 10_000
        self.ledger.move(asset_1_extra, self.asset_1_id, receiver=self.pool_address)
        self.ledger.move(asset_2_extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_1_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')

    def test_claim_algo_from_application_account(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        extra = 1_345
        self.ledger.move(extra, ALGO_ASSET_ID, receiver=APPLICATION_ADDRESS)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=ALGO_ASSET_ID, address=APPLICATION_ADDRESS, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [ALGO_ASSET_ID],
                b'apat': [decode_address(APPLICATION_ADDRESS), decode_address(fee_collector)],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': extra,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'pay',
            },
        )

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=ALGO_ASSET_ID, address=APPLICATION_ADDRESS, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')

    @skip
    def test_claim_pool_token_asset_from_application_account(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)
        self.ledger.opt_in_asset(fee_collector, self.pool_token_asset_id)
        self.ledger.opt_in_asset(APPLICATION_ADDRESS, self.pool_token_asset_id)

        extra = 100_000
        self.ledger.move(extra, self.pool_token_asset_id, sender=self.user_addr, receiver=APPLICATION_ADDRESS)
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.pool_token_asset_id, address=APPLICATION_ADDRESS, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(APPLICATION_ADDRESS), decode_address(fee_collector)],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                # b'aamt': extra,
                b'arcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'axfer',
                b'xaid': self.pool_token_asset_id
            },
        )
        self.ledger.get_account_balance(APPLICATION_ADDRESS, self.pool_token_asset_id)

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.pool_token_asset_id, address=APPLICATION_ADDRESS, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')


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

    def test_claim_asset_2_from_pool(self):
        fee_collector = self.app_creator_address
        self.ledger.set_account_balance(fee_collector, 1_000_000)

        extra = 10_000
        self.ledger.move(extra, self.asset_2_id, receiver=self.pool_address)

        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_2_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
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
                b'apas': [self.asset_2_id],
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
        self.assertEqual(len(inner_transactions), 1)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': extra,
                b'rcv': decode_address(fee_collector),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.pool_address),
                b'type': b'pay',
            },
        )

        # There is no extra to claim
        txn_group = self.get_claim_extra_transactions(sender=self.user_addr, asset_id=self.asset_2_id, address=self.pool_address, fee_collector=fee_collector, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_amount)')
