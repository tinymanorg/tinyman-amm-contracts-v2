from copy import deepcopy
from decimal import Decimal
from unittest.mock import ANY

from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import itob


class TestAddLiquidity(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()
        cls.asset_1_id = 5
        cls.asset_2_id = 2

    def reset_ledger(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(self.user_addr, 1_000_000)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.user_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        self.pool_address, self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, self.pool_token_asset_id)

    def setUp(self):
        self.reset_ledger()

    def test_initial_add_liquidity(self):
        test_cases = [
            dict(
                msg="Test adding initial liquidity (basic pass).",
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
                msg="Test adding minimum liquidity.",
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
                    source_line='assert(issued_pool_tokens > LOCKED_POOL_TOKENS)',
                )
            ),
            dict(
                msg="Test pool token out is negative.",
                inputs=dict(
                    asset_1_added_liquidity_amount=LOCKED_POOL_TOKENS - 1,
                    asset_2_added_liquidity_amount=LOCKED_POOL_TOKENS - 1,
                ),
                exception=dict(
                    source_line='assert(issued_pool_tokens > LOCKED_POOL_TOKENS)',
                )
            ),
        ]

        for test_case in test_cases:
            with self.subTest(**test_case):
                self.reset_ledger()

                inputs = test_case["inputs"]
                txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=inputs["asset_1_added_liquidity_amount"], asset_2_amount=inputs["asset_2_added_liquidity_amount"], app_call_fee=2_000)
                txn_group = transaction.assign_group_id(txn_group)
                stxns = self.sign_txns(txn_group, self.user_sk)

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
                            b'snd': decode_address(self.user_addr),
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
                            b'snd': decode_address(self.user_addr),
                            b'type': b'axfer',
                            b'xaid': self.asset_2_id
                        }
                    )

                    # outer transactions - [2]
                    txn = block_txns[2]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'apaa': [b'add_initial_liquidity'],
                            b'apas': [self.pool_token_asset_id],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee * 2,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(self.user_addr),
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
                            b'arcv': decode_address(self.user_addr),
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

    def test_subsequent_add_liquidity_2_assets(self):
        test_cases = [
            dict(
                msg="Add liquidity at exact 1:1 ratio.",
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
                    pool_tokens_out_amount=10_000,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
                )
            ),
            dict(
                msg="Add liquidity at almost exact 1:1 ratio (asset 2 +1).",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=10_000,
                    asset_2_added_liquidity_amount=10_001,
                ),
                outputs=dict(
                    pool_tokens_out_amount=10000,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
                )
            ),
            dict(
                msg="Add liquidity at almost exact 1:1 ratio (asset 2 -1).",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=1_000_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=10_000,
                    asset_2_added_liquidity_amount=9_999,
                ),
                outputs=dict(
                    pool_tokens_out_amount=9999,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
                )
            ),
            dict(
                msg="Add liquidity at a exact 1:1.25 ratio",
                initials=dict(
                    asset_1_reserves=1_000_000,
                    asset_2_reserves=1_250_000,
                    issued_pool_token_amount=1_118_033,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=10_000,
                    asset_2_added_liquidity_amount=12_500,
                ),
                outputs=dict(
                    pool_tokens_out_amount=11180,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
                )
            ),
            dict(
                msg="Flexible 1",
                initials=dict(
                    asset_1_reserves=100_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=10_000_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=5_432_198,
                    asset_2_added_liquidity_amount=1_234_567,
                ),
                outputs=dict(
                    pool_tokens_out_amount=5344406,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=228,
                )
            ),
            dict(
                msg="Flexible 2",
                initials=dict(
                    asset_1_reserves=100_000_000,
                    asset_2_reserves=1_000_000,
                    issued_pool_token_amount=10_000_000,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=1_234_567,
                    asset_2_added_liquidity_amount=5_432_198,
                ),
                outputs=dict(
                    pool_tokens_out_amount=15508778,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=762,
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
                    pool_tokens_out_amount=MAX_ASSET_AMOUNT - (LOCKED_POOL_TOKENS + 1),
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
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
                    pool_tokens_out_amount=MAX_ASSET_AMOUNT // 2,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
                )
            ),
            dict(
                msg="Test overflow by adding low liquidity to high liquidity pool.",
                initials=dict(
                    asset_1_reserves=MAX_ASSET_AMOUNT - 1,
                    asset_2_reserves=MAX_ASSET_AMOUNT - 1,
                    issued_pool_token_amount=MAX_ASSET_AMOUNT - 1,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=1,
                    asset_2_added_liquidity_amount=1,
                ),
                outputs=dict(
                    pool_tokens_out_amount=1,
                    asset_1_protocol_fees=0,
                    asset_2_protocol_fees=0,
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
                msg="Added liquidity is too small for the pool. The pool token out is 0.",
                initials=dict(
                    asset_1_reserves=10 ** 15,
                    asset_2_reserves=10 ** 3,
                    issued_pool_token_amount=10 ** 9,
                ),
                inputs=dict(
                    asset_1_added_liquidity_amount=1,
                    asset_2_added_liquidity_amount=0,
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

                self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=initials["asset_1_reserves"], asset_2_reserves=initials["asset_2_reserves"])
                self.assertEqual(initials["issued_pool_token_amount"], self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

                txn_group = self.get_add_liquidity_transactions(asset_1_amount=inputs["asset_1_added_liquidity_amount"], asset_2_amount=inputs["asset_2_added_liquidity_amount"], app_call_fee=3_000)
                txn_group = transaction.assign_group_id(txn_group)
                stxns = self.sign_txns(txn_group, self.user_sk)

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        self.ledger.eval_transactions(stxns)

                    self.assertEqual(e.exception.source['line'], exception.get("source_line"))

                else:
                    outputs = test_case["outputs"]

                    self.assertTrue(
                        outputs["pool_tokens_out_amount"] >= int(
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
                            b'snd': decode_address(self.user_addr),
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
                            b'snd': decode_address(self.user_addr),
                            b'type': b'axfer',
                            b'xaid': self.asset_2_id
                        }
                    )

                    # outer transactions - [2]
                    txn = block_txns[2]
                    self.assertDictEqual(
                        txn[b'txn'],
                        {
                            b'apaa': [b'add_liquidity', b'flexible', itob(0)],
                            b'apas': [self.pool_token_asset_id],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee * 3,
                            b'fv': self.sp.first,
                            b'grp': ANY,
                            b'lv': self.sp.last,
                            b'snd': decode_address(self.user_addr),
                            b'type': b'appl'
                        }
                    )

                    # inner transactions
                    inner_transactions = txn[b'dt'][b'itx']
                    self.assertEqual(len(inner_transactions), 2)

                    # inner transactions[1]
                    self.assertDictEqual(
                        inner_transactions[1][b'txn'],
                        {
                            b'aamt': outputs["pool_tokens_out_amount"],
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'arcv': decode_address(self.user_addr),
                            b'snd': decode_address(self.pool_address),
                            b'type': b'axfer',
                            b'xaid': self.pool_token_asset_id
                        }
                    )

                    # local state delta
                    pool_local_state_delta = txn[b'dt'][b'ld'][1]

                    if b'asset_1_protocol_fees' in pool_local_state_delta:
                        self.assertEqual(pool_local_state_delta[b'asset_1_protocol_fees'][b'ui'], outputs["asset_1_protocol_fees"])
                    else:
                        self.assertEqual(outputs["asset_1_protocol_fees"], 0)

                    if b'asset_2_protocol_fees' in pool_local_state_delta:
                        self.assertEqual(pool_local_state_delta[b'asset_2_protocol_fees'][b'ui'], outputs["asset_2_protocol_fees"])
                    else:
                        self.assertEqual(outputs["asset_1_protocol_fees"], 0)

                    self.assertEqual(pool_local_state_delta[b'asset_1_reserves'][b'ui'], initials["asset_1_reserves"] + inputs["asset_1_added_liquidity_amount"] - outputs["asset_1_protocol_fees"])
                    self.assertEqual(pool_local_state_delta[b'asset_2_reserves'][b'ui'], initials["asset_2_reserves"] + inputs["asset_2_added_liquidity_amount"] - outputs["asset_2_protocol_fees"])
                    self.assertEqual(pool_local_state_delta[b'issued_pool_tokens'][b'ui'], initials["issued_pool_token_amount"] + outputs["pool_tokens_out_amount"])

    def test_pass_subsequent_add_liquidity_asset_1(self):
        initial_asset_1_reserves = 10_000
        initial_asset_2_reserves = 15_000
        initial_issued_pool_token_amount = 12247
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = None
        pool_tokens_out_amount = 5067
        asset_1_protocol_fees = 2

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=initial_asset_1_reserves, asset_2_reserves=initial_asset_2_reserves)
        self.assertEqual(initial_issued_pool_token_amount, self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

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
                b'snd': decode_address(self.user_addr),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            }
        )

        # outer transactions - [1]
        txn = block_txns[1]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_liquidity', b'single', itob(0)],
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 3,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions[0] is a budget increase app call

        # inner transactions[1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': pool_tokens_out_amount,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'arcv': decode_address(self.user_addr),
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
                b'asset_1_cumulative_price': ANY,
                b'asset_1_protocol_fees': {b'at': 2, b'ui': asset_1_protocol_fees},
                b'asset_1_reserves': {b'at': 2, b'ui': initial_asset_1_reserves + (asset_1_added_liquidity_amount - asset_1_protocol_fees)},
                b'asset_2_cumulative_price': ANY,
                b'cumulative_price_update_timestamp': ANY,
                b'issued_pool_tokens': {b'at': 2, b'ui': initial_issued_pool_token_amount + pool_tokens_out_amount}
            }
        )

    def test_pass_subsequent_add_liquidity_asset_2(self):
        initial_asset_1_reserves = 15_000
        initial_asset_2_reserves = 10_000
        initial_issued_pool_token_amount = 12247
        asset_1_added_liquidity_amount = None
        asset_2_added_liquidity_amount = 10_000
        pool_tokens_out_amount = 5067
        asset_2_protocol_fees = 2

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=initial_asset_1_reserves, asset_2_reserves=initial_asset_2_reserves)
        self.assertEqual(initial_issued_pool_token_amount, self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'aamt': asset_2_added_liquidity_amount,
                b'arcv': decode_address(self.pool_address),
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
                b'type': b'axfer',
                b'xaid': self.asset_2_id,
            }
        )

        # outer transactions - [1]
        txn = block_txns[1]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_liquidity', b'single', itob(0)],
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 3,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions[0] is a budget increase app call

        # inner transactions[1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': pool_tokens_out_amount,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'arcv': decode_address(self.user_addr),
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
                b'asset_1_cumulative_price': ANY,
                b'asset_2_cumulative_price': ANY,
                b'asset_2_protocol_fees': {b'at': 2, b'ui': asset_2_protocol_fees},
                b'asset_2_reserves': {b'at': 2, b'ui': initial_asset_2_reserves + (asset_2_added_liquidity_amount - asset_2_protocol_fees)},
                b'cumulative_price_update_timestamp': ANY,
                b'issued_pool_tokens': {b'at': 2, b'ui': initial_issued_pool_token_amount + pool_tokens_out_amount}
            }
        )

    def test_fail_given_account_is_not_a_pool(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[2].accounts = [self.user_addr]
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'int asset_1_id = app_local_get(1, "asset_1_id")')

    def test_fail_wrong_asset_1_transfer(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[0].index, txn_group[1].index = txn_group[1].index, txn_group[0].index
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].XferAsset == asset_1_id)')

    def test_fail_wrong_asset_2_transfer(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[1].index = self.asset_1_id
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].XferAsset == asset_2_id)')

    def test_fail_output_below_min_liquidity(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000
        min_output = 100_000  # Much too high, should fail
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=100_000, asset_2_reserves=150_000)

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, min_output=min_output)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(pool_tokens_out >= min_output)')

    def test_asset_receivers_are_not_pool(self):
        _, asset_receiver = generate_account()
        self.ledger.set_account_balance(asset_receiver, 1_000_000)
        self.ledger.opt_in_asset(asset_receiver, asset_id=self.asset_1_id)
        self.ledger.opt_in_asset(asset_receiver, asset_id=self.asset_2_id)

        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        # Asset 1 Receiver
        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[0].receiver = asset_receiver
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].AssetReceiver == pool_address)')

        # Asset 2 Receiver
        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[1].receiver = asset_receiver
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].AssetReceiver == pool_address)')

    def test_fail_output_below_min_subsequent_liquidity(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000
        min_output = 100_000  # Much too high, should fail

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=100_000, asset_2_reserves=150_000)

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, min_output=min_output)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(pool_tokens_out >= min_output)')

    def test_fail_app_caller_and_asset_1_sender_is_not_equal(self):
        new_sk, new_addr = generate_account()
        self.ledger.set_account_balance(new_addr, 1_000_000)
        self.ledger.set_account_balance(new_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(new_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[0].sender = new_addr
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(new_sk),
            txn_group[1].sign(self.user_sk),
            txn_group[2].sign(self.user_sk),
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].Sender == user_address)')

    def test_fail_app_caller_and_asset_2_sender_is_not_equal(self):
        new_sk, new_addr = generate_account()
        self.ledger.set_account_balance(new_addr, 1_000_000)
        self.ledger.set_account_balance(new_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(new_addr, MAX_ASSET_AMOUNT, asset_id=self.asset_2_id)

        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[1].sender = new_addr
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(new_sk),
            txn_group[2].sign(self.user_sk),
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Sender == user_address)')


class TestAddLiquidityAlgoPair(BaseTestCase):

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
        self.ledger.set_account_balance(self.user_addr, 2_000_000)
        self.ledger.set_account_balance(self.user_addr, 1_000_000, asset_id=self.asset_1_id)

        self.pool_address, self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)
        self.ledger.opt_in_asset(self.user_addr, self.pool_token_asset_id)

    def test_pass_initial_add_liquidity(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000
        issued_pool_token_amount = 12247    # int(sqrt(10_000 * 15_000))
        pool_tokens_out_amount = issued_pool_token_amount - LOCKED_POOL_TOKENS

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=2_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'snd': decode_address(self.user_addr),
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
                b'snd': decode_address(self.user_addr),
                b'type': b'pay',
            }
        )

        # outer transactions - [2]
        txn = block_txns[2]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_initial_liquidity'],
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 2,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
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
                b'arcv': decode_address(self.user_addr),
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

    def test_algo_receiver_is_not_pool(self):
        _, asset_receiver = generate_account()
        self.ledger.set_account_balance(asset_receiver, 1_000_000)

        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        # Algo Receiver
        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[1].receiver = asset_receiver
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Receiver == pool_address)')

    def test_fail_wrong_asset_2_transfer(self):
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[1] = deepcopy(txn_group[0])
        txn_group[1].amount = asset_2_added_liquidity_amount
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].TypeEnum == Pay)')

    def test_fail_app_caller_and_asset_2_sender_is_not_equal(self):
        new_sk, new_addr = generate_account()
        self.ledger.set_account_balance(new_addr, 1_000_000)

        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000

        txn_group = self.get_add_initial_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount)
        txn_group[1].sender = new_addr
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(new_sk),
            txn_group[2].sign(self.user_sk),
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Sender == user_address)')

    def test_pass_subsequent_add_liquidity_2_assets(self):
        initial_asset_1_reserves = 10_000
        initial_asset_2_reserves = 15_000
        initial_issued_pool_token_amount = 12247
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = 15_000
        pool_tokens_out_amount = 12247

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=initial_asset_1_reserves, asset_2_reserves=initial_asset_2_reserves)
        self.assertEqual(initial_issued_pool_token_amount, self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

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
                b'snd': decode_address(self.user_addr),
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
                b'snd': decode_address(self.user_addr),
                b'type': b'pay',
            }
        )

        # outer transactions - [2]
        txn = block_txns[2]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_liquidity', b'flexible', itob(0)],
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 3,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions[0] is a budget increase app call

        # inner transactions[1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': pool_tokens_out_amount,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'arcv': decode_address(self.user_addr),
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
                b'asset_1_cumulative_price': ANY,
                b'asset_1_reserves': {b'at': 2, b'ui': initial_asset_1_reserves + asset_1_added_liquidity_amount},
                b'asset_2_reserves': {b'at': 2, b'ui': initial_asset_2_reserves + asset_2_added_liquidity_amount},
                b'asset_2_cumulative_price': ANY,
                b'cumulative_price_update_timestamp': ANY,
                b'issued_pool_tokens': {b'at': 2, b'ui': initial_issued_pool_token_amount + 12247}
            }
        )

    def test_pass_subsequent_add_liquidity_asset_1(self):
        initial_asset_1_reserves = 10_000
        initial_asset_2_reserves = 15_000
        initial_issued_pool_token_amount = 12247
        asset_1_added_liquidity_amount = 10_000
        asset_2_added_liquidity_amount = None
        pool_tokens_out_amount = 5067
        asset_1_protocol_fees = 2

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=initial_asset_1_reserves, asset_2_reserves=initial_asset_2_reserves)
        self.assertEqual(initial_issued_pool_token_amount, self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

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
                b'snd': decode_address(self.user_addr),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            }
        )

        # outer transactions - [1]
        txn = block_txns[1]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_liquidity', b'single', itob(0)],
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 3,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions[0] is a budget increase app call

        # inner transactions[1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': pool_tokens_out_amount,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'arcv': decode_address(self.user_addr),
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
                b'asset_1_cumulative_price': ANY,
                b'asset_1_protocol_fees': {b'at': 2, b'ui': asset_1_protocol_fees},
                b'asset_1_reserves': {b'at': 2, b'ui': initial_asset_1_reserves + (asset_1_added_liquidity_amount - asset_1_protocol_fees)},
                b'asset_2_cumulative_price': ANY,
                b'cumulative_price_update_timestamp': ANY,
                b'issued_pool_tokens': {b'at': 2, b'ui': initial_issued_pool_token_amount + pool_tokens_out_amount}
            }
        )

    def test_pass_subsequent_add_liquidity_asset_2(self):
        initial_asset_1_reserves = 15_000
        initial_asset_2_reserves = 10_000
        initial_issued_pool_token_amount = 12247
        asset_1_added_liquidity_amount = None
        asset_2_added_liquidity_amount = 10_000
        pool_tokens_out_amount = 5067
        asset_2_protocol_fees = 2

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=initial_asset_1_reserves, asset_2_reserves=initial_asset_2_reserves)
        self.assertEqual(initial_issued_pool_token_amount, self.ledger.accounts[self.pool_address]['local_states'][APPLICATION_ID][b'issued_pool_tokens'])

        txn_group = self.get_add_liquidity_transactions(asset_1_amount=asset_1_added_liquidity_amount, asset_2_amount=asset_2_added_liquidity_amount, app_call_fee=3_000)
        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'amt': asset_2_added_liquidity_amount,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'rcv': decode_address(self.pool_address),
                b'snd': decode_address(self.user_addr),
                b'type': b'pay',
            }
        )

        # outer transactions - [1]
        txn = block_txns[1]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'add_liquidity', b'single', itob(0)],
                b'apas': [self.pool_token_asset_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee * 3,
                b'fv': self.sp.first,
                b'grp': ANY,
                b'lv': self.sp.last,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 2)

        # inner transactions[0] is a budget increase app call

        # inner transactions[1]
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'aamt': pool_tokens_out_amount,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'arcv': decode_address(self.user_addr),
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
                b'asset_1_cumulative_price': ANY,
                b'asset_2_protocol_fees': {b'at': 2, b'ui': asset_2_protocol_fees},
                b'asset_2_reserves': {b'at': 2, b'ui': initial_asset_2_reserves + (asset_2_added_liquidity_amount - asset_2_protocol_fees)},
                b'asset_2_cumulative_price': ANY,
                b'cumulative_price_update_timestamp': ANY,
                b'issued_pool_tokens': {b'at': 2, b'ui': initial_issued_pool_token_amount + pool_tokens_out_amount}
            }
        )
