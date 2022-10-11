from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase


class TestSetFee(BaseTestCase):
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

    def test_set_fee(self):
        test_cases = [
            dict(
                msg="Test maximum.",
                inputs=dict(
                    total_fee_share=100,
                    protocol_fee_ratio=10
                ),
            ),
            dict(
                msg="Test minimums.",
                inputs=dict(
                    total_fee_share=1,
                    protocol_fee_ratio=3
                ),
            ),
            dict(
                msg="Test total share upper bound.",
                inputs=dict(
                    total_fee_share=101,
                    protocol_fee_ratio=10
                ),
                exception=dict(
                    source_line='assert(total_fee_share <= 100)',
                )
            ),
            dict(
                msg="Test total share lower bound.",
                inputs=dict(
                    total_fee_share=0,
                    protocol_fee_ratio=10
                ),
                exception=dict(
                    source_line='assert(total_fee_share >= 1)',
                )
            ),
            dict(
                msg="Test protocol ratio upper bound.",
                inputs=dict(
                    total_fee_share=50,
                    protocol_fee_ratio=11
                ),
                exception=dict(
                    source_line='assert(protocol_fee_ratio <= 10)',
                )
            ),
            dict(
                msg="Test protocol ratio lower bound.",
                inputs=dict(
                    total_fee_share=50,
                    protocol_fee_ratio=2
                ),
                exception=dict(
                    source_line='assert(protocol_fee_ratio >= 3)',
                )
            ),
        ]

        for test_case in test_cases:
            with self.subTest(**test_case):
                self.reset_ledger()
                inputs = test_case["inputs"]

                stxns = [
                    transaction.ApplicationNoOpTxn(
                        sender=self.app_creator_address,
                        sp=self.sp,
                        index=APPLICATION_ID,
                        app_args=[METHOD_SET_FEE, inputs["total_fee_share"], inputs["protocol_fee_ratio"]],
                        accounts=[self.pool_address],
                    ).sign(self.app_creator_sk)
                ]

                if exception := test_case.get("exception"):
                    with self.assertRaises(LogicEvalError) as e:
                        self.ledger.eval_transactions(stxns)

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
                            b'apaa': [b'set_fee', inputs["total_fee_share"].to_bytes(8, 'big'), inputs["protocol_fee_ratio"].to_bytes(8, 'big')],
                            b'apat': [decode_address(self.pool_address)],
                            b'apid': APPLICATION_ID,
                            b'fee': self.sp.fee,
                            b'fv': self.sp.first,
                            b'lv': self.sp.last,
                            b'snd': decode_address(self.app_creator_address),
                            b'type': b'appl'
                        }
                    )

                    # outer transactions[0] - Pool State Delta
                    self.assertDictEqual(
                        txn[b'dt'][b'ld'],
                        {
                            1: {
                                b'total_fee_share': {b'at': 2, **({b'ui': inputs["total_fee_share"]} if inputs["total_fee_share"] else {})},
                                b'protocol_fee_ratio': {b'at': 2, **({b'ui': inputs["protocol_fee_ratio"]} if inputs["protocol_fee_ratio"] else {})}
                            }
                        }
                    )

    def test_sender(self):
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

        # Sender is not fee setter (app creator default)
        new_account_sk, new_account_address = generate_account()
        self.ledger.set_account_balance(new_account_address, 1_000_000)
        total_fee_share = 10
        protocol_fee_ratio = 3
        stxns = [
            transaction.ApplicationNoOpTxn(
                sender=new_account_address,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE, total_fee_share, protocol_fee_ratio],
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
                b'apaa': [b'set_fee', total_fee_share.to_bytes(8, 'big'), protocol_fee_ratio.to_bytes(8, 'big')],
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
                    b'total_fee_share': {b'at': 2, b'ui': total_fee_share},
                    b'protocol_fee_ratio': {b'at': 2, b'ui': protocol_fee_ratio}
                }
            }
        )
