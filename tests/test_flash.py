from unittest.mock import ANY

from algojig import get_suggested_params
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.atomic_transaction_composer import AtomicTransactionComposer, TransactionWithSigner, AccountTransactionSigner
from algosdk.encoding import decode_address
from algosdk.future import transaction
from algosdk.future.transaction import SuggestedParams

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode


class TestFlash(BaseTestCase):

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
        self.ledger.set_account_balance(self.user_addr, 1_000_000, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()

    def test_flash_asset_1_and_asset_2_pass(self):
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

        asset_1_amount = 1000
        asset_2_amount = 2000
        index_diff = 3
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 3000
        txn_group[3].fee = 3000

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
                    b'flash',
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
        self.assertEqual(set(txn[b'dt'][b'ld'][1].keys()), {b'cumulative_asset_1_price', b'cumulative_asset_2_price', b'cumulative_price_update_timestamp'})

        # Verify Flash
        # outer transactions - [3]
        txn = block_txns[3]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'verify_flash',
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
                    b'aamt': 8997,
                    b'arcv': decode_address(self.user_addr),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(self.pool_address),
                    b'type': b'axfer',
                    b'xaid': self.asset_1_id
                }
            },
        )
        self.assertDictEqual(
            inner_transactions[1],
            {
                b'txn': {
                    b'aamt': 7994,
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
        self.assertDictEqual(
            txn[b'dt'][b'ld'][1],
            {
                b'asset_1_reserves': {b'at': 2, b'ui': 1000003},
                b'asset_2_reserves': {b'at': 2, b'ui': 1000005},
                b'protocol_fees_asset_2': {b'at': 2, b'ui': 1}}
        )

    def test_flash_asset_1_pass(self):
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

        asset_1_amount = 4001
        asset_1_repayment_amount = asset_1_amount * 10030 // 10000
        asset_1_repayment_txn_amount = 10_000

        assert_1_change = asset_1_repayment_txn_amount - asset_1_repayment_amount
        asset_1_reserves = 1000_010
        protocol_fees_asset_1 = 2

        asset_2_amount = 0
        index_diff = 2
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_FLASH, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            ),
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_repayment_txn_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_VERIFY_FLASH, index_diff, asset_1_amount, asset_2_amount],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[0].fee = 2000
        txn_group[2].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 3)

        # Flash
        # outer transactions - [0]
        txn = block_txns[0]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'flash',
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
        self.assertEqual(set(txn[b'dt'][b'ld'][1].keys()), {b'cumulative_asset_1_price', b'cumulative_asset_2_price', b'cumulative_price_update_timestamp'})

        # Verify Flash
        # outer transactions - [2]
        txn = block_txns[2]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    b'verify_flash',
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
                    b'aamt': assert_1_change,
                    b'arcv': decode_address(self.user_addr),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(self.pool_address),
                    b'type': b'axfer',
                    b'xaid': self.asset_1_id
                }
            },
        )

        # local delta, only price oracle is updated
        self.assertDictEqual(
            txn[b'dt'][b'ld'][1],
            {
                b'asset_1_reserves': {b'at': 2, b'ui': asset_1_reserves},
                b'protocol_fees_asset_1': {b'at': 2, b'ui': protocol_fees_asset_1}
            }
        )

    def test_abi_flash(self):
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

        # Check method selectors
        method = contract.get_method_by_name(METHOD_FLASH)
        self.assertEqual(method.get_selector(), ABI_METHOD[METHOD_FLASH])
        method = contract.get_method_by_name(METHOD_VERIFY_FLASH)
        self.assertEqual(method.get_selector(), ABI_METHOD[METHOD_VERIFY_FLASH])

        user_signer = AccountTransactionSigner(self.user_sk)
        asset_1_amount = 1000
        asset_2_amount = 2000
        index_diff = 3

        composer = AtomicTransactionComposer()
        composer.add_method_call(
            app_id=APPLICATION_ID,
            method=contract.get_method_by_name(METHOD_FLASH),
            sender=self.user_addr,
            sp=SuggestedParams(**{**self.sp.__dict__, **{"fee": 3000}}),
            signer=user_signer,
            method_args=[
                index_diff,
                asset_1_amount,
                asset_2_amount,
                self.asset_1_id,
                self.asset_2_id,
                self.pool_address
            ],
        )
        composer.add_transaction(
            TransactionWithSigner(
                txn=transaction.AssetTransferTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    receiver=self.pool_address,
                    index=self.asset_1_id,
                    amt=10_000,
                ),
                signer=user_signer,
            )
        )
        composer.add_transaction(
            TransactionWithSigner(
                txn=transaction.AssetTransferTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    receiver=self.pool_address,
                    index=self.asset_2_id,
                    amt=10_000,
                ),
                signer=user_signer,
            )
        )
        composer.add_method_call(
            app_id=APPLICATION_ID,
            method=contract.get_method_by_name(METHOD_VERIFY_FLASH),
            sender=self.user_addr,
            sp=SuggestedParams(**{**self.sp.__dict__, **{"fee": 3000}}),
            signer=user_signer,
            method_args=[
                index_diff,
                asset_1_amount,
                asset_2_amount,
                self.asset_1_id,
                self.asset_2_id,
                self.pool_address
            ],
        )

        composer.gather_signatures()
        block = self.ledger.eval_transactions(composer.signed_txns)
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
                    ABI_METHOD[METHOD_FLASH],
                    index_diff.to_bytes(8, "big"),
                    asset_1_amount.to_bytes(8, "big"),
                    asset_2_amount.to_bytes(8, "big"),
                    # Assets
                    (0).to_bytes(1, "big"),
                    (1).to_bytes(1, "big"),
                    # Accounts
                    (1).to_bytes(1, "big"),
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
        self.assertEqual(set(txn[b'dt'][b'ld'][1].keys()), {b'cumulative_asset_1_price', b'cumulative_asset_2_price', b'cumulative_price_update_timestamp'})

        # Verify Flash
        # outer transactions - [3]
        txn = block_txns[3]
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [
                    ABI_METHOD[METHOD_VERIFY_FLASH],
                    index_diff.to_bytes(8, "big"),
                    asset_1_amount.to_bytes(8, "big"),
                    asset_2_amount.to_bytes(8, "big"),
                    # Assets
                    (0).to_bytes(1, "big"),
                    (1).to_bytes(1, "big"),
                    # Accounts
                    (1).to_bytes(1, "big"),
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
                    b'aamt': 8997,
                    b'arcv': decode_address(self.user_addr),
                    b'fv': ANY,
                    b'lv': ANY,
                    b'snd': decode_address(self.pool_address),
                    b'type': b'axfer',
                    b'xaid': self.asset_1_id
                }
            },
        )
        self.assertDictEqual(
            inner_transactions[1],
            {
                b'txn': {
                    b'aamt': 7994,
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
        self.assertDictEqual(
            txn[b'dt'][b'ld'][1],
            {
                b'asset_1_reserves': {b'at': 2, b'ui': 1000003},
                b'asset_2_reserves': {b'at': 2, b'ui': 1000005},
                b'protocol_fees_asset_2': {b'at': 2, b'ui': 1}}
        )

# TODO Test AlgoPair.
