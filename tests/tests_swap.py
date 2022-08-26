from unittest.mock import ANY

from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode, int_to_bytes_without_zero_padding


class TestSwap(BaseTestCase):

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
        self.ledger.set_account_balance(self.user_addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.user_addr, 0, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()

    def test_fixed_input_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        min_output = 9000
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, min_output, "fixed-input"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'aamt': 10000,
                b'arcv': decode_address(self.pool_address),
                b'fee': ANY,
                b'fv': ANY,
                b'lv': ANY,
                b'grp': ANY,
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
                b'apaa': [
                    b'swap',
                    self.asset_1_id.to_bytes(8, 'big'),
                    self.asset_2_id.to_bytes(8, 'big'),
                    min_output.to_bytes(8, 'big'),
                    b'fixed-input'
                ],
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': 2000,
                b'fv': ANY,
                b'lv': ANY,
                b'grp': ANY,
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
                b'aamt': 9871,
                b'arcv': decode_address(self.user_addr),
                b'fv': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            }
        )

        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'asset_1_reserves': {b'at': 2, b'ui': 1009995},
                b'asset_2_reserves': {b'at': 2, b'ui': 990129},
                b'protocol_fees_asset_1': {b'at': 2, b'ui': 5},
                b'cumulative_asset_1_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(PRICE_SCALE_FACTOR * BLOCK_TIME_DELTA)},
                b'cumulative_asset_2_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(PRICE_SCALE_FACTOR * BLOCK_TIME_DELTA)},
                b'cumulative_price_update_timestamp': {b'at': 2, b'ui': BLOCK_TIME_DELTA},
            }
        )

    def test_fixed_output_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        amount_out = 9871
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, amount_out, "fixed-output"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 3000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 1)

        # Check details of output inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], amount_out)
        self.assertEqual(itxn0[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn0[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

    def test_fixed_output_with_change_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        amount_out = 9872
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_100,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, amount_out, "fixed-output"],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 3000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 2)

        # Check details of input change inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 99)
        self.assertEqual(itxn0[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn0[b'xaid'], self.asset_1_id)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

        # Check details of output inner transaction
        itxn1 = txns[1][b'dt'][b'itx'][1][b'txn']
        self.assertEqual(itxn1[b'aamt'], amount_out)
        self.assertEqual(itxn1[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn1[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn1[b'snd'], decode_address(self.pool_address))

    def test_fail_insufficient_fee(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
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
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('fee too small', e.exception.error)

    def test_fail_wrong_asset_in(self):
        self.ledger.set_account_balance(self.user_addr, 1_000_000, asset_id=0)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        txn_group = [
            transaction.PaymentTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
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
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('assert failed', e.exception.error)

    def test_fail_wrong_asset_out_1(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
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
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)

    def test_fail_wrong_asset_out_2(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=self.asset_2_id)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': 1_000_000,
                b'asset_2_reserves': 1_000_000,
                b'issued_pool_tokens': 1_000_000,
            }
        )

        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
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
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)
