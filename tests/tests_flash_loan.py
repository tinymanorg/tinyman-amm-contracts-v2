from unittest.mock import ANY

from algojig import get_suggested_params, LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase


dummy_program = TealishProgram('tests/dummy_program.tl')
DUMMY_APP_ID = 11


class TestFlashLoan(BaseTestCase):

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
        self.ledger.set_account_balance(self.user_addr, 100_000_000)
        self.ledger.set_account_balance(self.user_addr, 100_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.user_addr, 100_000_000, asset_id=self.asset_2_id)

        self.pool_address, self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)

    def test_flash_loan_asset_1_and_asset_2_pass(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=100_000_000, asset_2_reserves=100_000_000)

        asset_1_amount = 10_000_000
        asset_2_amount = 20_000_000
        index_diff = 3
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=15_000_000,
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=25_000_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 3000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 4)

        # Flash
        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'flash_loan',
                    index_diff.to_bytes(8, "big"),
                    asset_1_amount.to_bytes(8, "big"),
                    asset_2_amount.to_bytes(8, "big"),
                ],
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apat': [decode_address(self.pool_address)],
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
        self.assertDictEqual(
            inner_transactions[0],
            {
                b'txn': {
                    b'aamt': asset_1_amount,
                    b'arcv': decode_address(self.user_addr),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(self.pool_address),
                    b'type': b'axfer',
                    b'xaid': self.asset_1_id
                }
            }
        )
        self.assertDictEqual(
            inner_transactions[1],
            {
                b'txn': {
                    b'aamt': asset_2_amount,
                    b'arcv': decode_address(self.user_addr),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(self.pool_address),
                    b'type': b'axfer',
                    b'xaid': self.asset_2_id
                }
            }
        )

        # local delta, only price oracle is updated
        txn[b'dt'][b'ld'][1].keys()
        self.assertEqual(set(txn[b'dt'][b'ld'][1].keys()), {b'asset_1_cumulative_price', b'asset_2_cumulative_price', b'cumulative_price_update_timestamp'})

        # Verify Flash
        # outer transactions - [3]
        txn = block_txns[3]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'verify_flash_loan',
                    index_diff.to_bytes(8, "big"),
                ],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # local delta, only price oracle is updated
        self.assertDictEqual(
            txn[b'dt'][b'ld'][1],
            {
                b'asset_1_protocol_fees': {b'at': 2, b'ui': 5000},
                b'asset_1_reserves': {b'at': 2, b'ui': 100025000},
                b'asset_2_protocol_fees': {b'at': 2, b'ui': 10000},
                b'asset_2_reserves': {b'at': 2, b'ui': 100050000}
            }
        )
        # Logs
        self.assertListEqual(
            txn[b'dt'][b'lg'],
            [
                bytes(bytearray(b'asset_1_output_amount %i') + bytearray((10000000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_input_amount %i') + bytearray((15000000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_donation_amount %i') + bytearray((4970000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_poolers_fee_amount %i') + bytearray((25000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_protocol_fee_amount %i') + bytearray((5000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_total_fee_amount %i') + bytearray((30000).to_bytes(8, "big"))),

                bytes(bytearray(b'asset_2_output_amount %i') + bytearray((20000000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_2_input_amount %i') + bytearray((25000000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_2_donation_amount %i') + bytearray((4940000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_2_poolers_fee_amount %i') + bytearray((50000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_2_protocol_fee_amount %i') + bytearray((10000).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_2_total_fee_amount %i') + bytearray((60000).to_bytes(8, "big"))),
            ]
        )

    def test_flash_loan_asset_1_pass(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 4001
        asset_1_repayment_amount = asset_1_amount * 10030 // 10000

        asset_1_reserves = 1000_010
        asset_1_protocol_fees = 2

        asset_2_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 3)

        # Flash Loan
        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'flash_loan',
                    index_diff.to_bytes(8, "big"),
                    asset_1_amount.to_bytes(8, "big"),
                    asset_2_amount.to_bytes(8, "big"),
                ],
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apat': [decode_address(self.pool_address)],
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
        self.assertDictEqual(
            inner_transactions[0],
            {
                b'txn': {
                    b'aamt': asset_1_amount,
                    b'arcv': decode_address(self.user_addr),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(self.pool_address),
                    b'type': b'axfer',
                    b'xaid': self.asset_1_id
                }
            }
        )

        # local delta, only price oracle is updated
        txn[b'dt'][b'ld'][1].keys()
        self.assertEqual(set(txn[b'dt'][b'ld'][1].keys()), {b'asset_1_cumulative_price', b'asset_2_cumulative_price', b'cumulative_price_update_timestamp'})

        # Verify Flash Loan
        # outer transactions - [2]
        txn = block_txns[2]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'verify_flash_loan',
                    index_diff.to_bytes(8, "big"),
                ],
                b'apat': [decode_address(self.pool_address)],
                b'apid': APPLICATION_ID,
                b'fee': ANY,
                b'fv': ANY,
                b'grp': ANY,
                b'lv': ANY,
                b'snd': decode_address(self.user_addr),
                b'type': b'appl'
            }
        )

        # local delta, only price oracle is updated
        self.assertDictEqual(
            txn[b'dt'][b'ld'][1],
            {
                b'asset_1_reserves': {b'at': 2, b'ui': asset_1_reserves},
                b'asset_1_protocol_fees': {b'at': 2, b'ui': asset_1_protocol_fees}
            }
        )

        # Logs
        self.assertListEqual(
            txn[b'dt'][b'lg'],
            [
                bytes(bytearray(b'asset_1_output_amount %i') + bytearray((4001).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_input_amount %i') + bytearray((4013).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_donation_amount %i') + bytearray((0).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_poolers_fee_amount %i') + bytearray((10).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_protocol_fee_amount %i') + bytearray((2).to_bytes(8, "big"))),
                bytes(bytearray(b'asset_1_total_fee_amount %i') + bytearray((12).to_bytes(8, "big"))),
            ]
        )

    def test_fail_insufficient_repayment_asset_1(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 4001
        asset_1_repayment_amount = (asset_1_amount * 10030 // 10000) - 1
        asset_2_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].AssetAmount >= asset_1_repayment_amount)')

    def test_fail_insufficient_repayment_asset_2(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_2_amount = 4001
        asset_2_repayment_amount = (asset_2_amount * 10030 // 10000) - 1
        asset_1_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=asset_2_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].AssetAmount >= asset_2_repayment_amount)')

    def test_fail_wrong_asset_repayment_asset_1(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 4001
        asset_1_repayment_amount = (asset_1_amount * 10030 // 10000) - 1
        asset_2_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=asset_1_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].XferAsset == asset_1_id)')

    def test_fail_wrong_asset_repayment_asset_2(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_2_amount = 4001
        asset_2_repayment_amount = (asset_2_amount * 10030 // 10000) - 1
        asset_1_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_2_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].XferAsset == asset_2_id)')

    def test_fail_asset_senders_are_not_same(self):
        new_user_sk, new_user_addr = generate_account()
        self.ledger.set_account_balance(new_user_addr, 1_000_000)
        self.ledger.set_account_balance(new_user_addr, 1_000_000, self.asset_1_id)
        self.ledger.set_account_balance(new_user_addr, 1_000_000, self.asset_2_id)

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 10_000
        asset_2_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=new_user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=15_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(new_user_sk),
            txn_group[2].sign(self.user_sk)
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].Sender == user_address)')

        asset_1_amount = 0
        asset_2_amount = 10_000
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=new_user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=15_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(new_user_sk),
            txn_group[2].sign(self.user_sk)
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Sender == user_address)')

    def test_fail_asset_receiver_is_not_pool_asset_1(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 2
        asset_1_amount = 1_000
        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.user_addr,
                index=self.asset_1_id,
                amt=asset_1_amount * 10,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_1_txn_index].AssetReceiver == pool_address)')

    def test_fail_asset_receiver_is_not_pool_asset_2(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 2
        asset_2_amount = 1_000
        asset_1_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.user_addr,
                index=self.asset_2_id,
                amt=asset_2_amount * 10,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].AssetReceiver == pool_address)')

    def test_fail_app_call_senders_are_not_same(self):
        new_user_sk, new_user_addr = generate_account()
        self.ledger.set_account_balance(new_user_addr, 1_000_000)
        self.ledger.set_account_balance(new_user_addr, 1_000_000, self.asset_1_id)

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 4001
        asset_1_repayment_amount = asset_1_amount * 10030 // 10000

        asset_2_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=new_user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(new_user_sk),
            txn_group[1].sign(self.user_sk),
            txn_group[2].sign(self.user_sk)
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[verify_flash_loan_txn_index].Sender == user_address)')

    def test_fail_different_index_diffs(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 4001
        asset_1_repayment_amount = asset_1_amount * 10030 // 10000

        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, 2, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, 1],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[verify_flash_loan_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])')

    def test_fail_index_diff_check(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 1
        asset_1_amount = 10_000
        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(index_diff > 1)')

        index_diff = 2
        asset_1_amount = 10_000
        asset_2_amount = 10_000
        asset_1_repayment_amount = 15_000
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(index_diff > 2)')

    def test_fail_amounts_are_zero(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 2
        asset_1_amount = 0
        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=0,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_amount || asset_2_amount)')

    def test_fail_application_ids_are_not_same(self):
        self.ledger.create_app(app_id=DUMMY_APP_ID, approval_program=dummy_program)
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 2
        asset_1_amount = 1_000
        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_amount * 10,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=DUMMY_APP_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[verify_flash_loan_txn_index].ApplicationID == Global.CurrentApplicationID)')

    def test_fail_wrong_verification_method_name(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 2
        asset_1_amount = 1_000
        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_amount * 10,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN + "-invalid", index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[verify_flash_loan_txn_index].ApplicationArgs[0] == "verify_flash_loan")')

    def test_fail_verify_call_different_index_diffs(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 3
        asset_1_amount = 1_000
        asset_2_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_amount * 10,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, 2],
                foreign_assets=[],
                accounts=[self.pool_address],
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[flash_loan_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])')

    def test_fail_pools_are_not_same_in_app_calls(self):
        other_pool_asset_1_id = self.ledger.create_asset(asset_id=None)
        other_pool_address, other_pool_token_asset_id = self.bootstrap_pool(other_pool_asset_1_id, self.asset_2_id)

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)
        self.set_initial_pool_liquidity(other_pool_address, other_pool_asset_1_id, self.asset_2_id, other_pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 0
        asset_2_amount = 4000
        asset_2_repayment_amount = asset_2_amount * 10030 // 10000

        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=other_pool_address,
                index=self.asset_2_id,
                amt=asset_2_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[other_pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[verify_flash_loan_txn_index].Accounts[1] == Txn.Accounts[1])')


class TestFlashLoanAlgoPair(BaseTestCase):

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
        self.ledger.set_account_balance(self.user_addr, 200_000_000, asset_id=self.asset_2_id)
        self.ledger.set_account_balance(self.user_addr, 100_000_000, asset_id=self.asset_1_id)

        self.pool_address, self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)

    def test_fail_insufficient_repayment_algo(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_2_amount = 4001
        asset_2_repayment_amount = (asset_2_amount * 10030 // 10000) - 1
        asset_1_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.PaymentTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=asset_2_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Amount >= asset_2_repayment_amount)')

    def test_fail_wrong_asset_repayment_algo(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_2_amount = 4001
        asset_2_repayment_amount = (asset_2_amount * 10030 // 10000) - 1
        asset_1_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_2_repayment_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].TypeEnum == Pay)')

    def test_fail_algo_sender_is_not_same(self):
        new_user_sk, new_user_addr = generate_account()
        self.ledger.set_account_balance(new_user_addr, 1_000_000)
        self.ledger.set_account_balance(new_user_addr, 1_000_000, self.asset_1_id)
        self.ledger.set_account_balance(new_user_addr, 1_000_000, self.asset_2_id)

        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        asset_1_amount = 0
        asset_2_amount = 10_000
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.PaymentTxn(
                sender=new_user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=15_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(new_user_sk),
            txn_group[2].sign(self.user_sk)
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Sender == user_address)')

    def test_fail_asset_receiver_is_not_pool_algo(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        index_diff = 2
        asset_2_amount = 1_000
        asset_1_amount = 0
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH_LOAN, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.PaymentTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.user_addr,
                amt=asset_2_amount * 10,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH_LOAN, index_diff],
                foreign_assets=[],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Gtxn[asset_2_txn_index].Receiver == pool_address)')
