
from algojig import get_suggested_params
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode


class TestGroupedSwap(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.asset_3_id = 7

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.ledger.set_account_balance(self.user_addr, 1_000_000)
        self.ledger.set_account_balance(self.user_addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.user_addr, 0, asset_id=self.asset_2_id)
        self.ledger.set_account_balance(self.user_addr, 0, asset_id=self.asset_3_id)

        lsig1 = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id)
        self.pool_address1 = lsig1.address()
        self.ledger.set_account_balance(self.pool_address1, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address1, APPLICATION_ADDRESS)

        lsig2 = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_2_id, self.asset_3_id)
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
                b'total_fee_share': TOTAL_FEE_SHARE,
                b'protocol_fee_ratio': PROTOCOL_FEE_RATIO,
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
                b'total_fee_share': TOTAL_FEE_SHARE,
                b'protocol_fee_ratio': PROTOCOL_FEE_RATIO,
            }
        )

        swap_1_amount_out = 9871
        swap_2_amount_out = 9746
        txn_group = [
            # Swap 1
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address1,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, "fixed-input", swap_1_amount_out],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address1],
            ),

            # Swap 2
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address2,
                index=self.asset_2_id,
                amt=swap_1_amount_out,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, "fixed-input", swap_2_amount_out],
                foreign_assets=[self.asset_2_id, self.asset_3_id],
                accounts=[self.pool_address2],
            )
        ]
        txn_group[1].fee = 5000
        txn_group[3].fee = 5000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = self.sign_txns(txn_group, self.user_sk)
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']

        itxn = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn[b'aamt'], swap_1_amount_out)
        self.assertEqual(itxn[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn[b'snd'], decode_address(self.pool_address1))

        itxn = txns[3][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn[b'aamt'], swap_2_amount_out)
        self.assertEqual(itxn[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_3_id)
        self.assertEqual(itxn[b'snd'], decode_address(self.pool_address2))

        self.assertEqual(self.ledger.get_account_balance(self.user_addr, self.asset_2_id)[0], 0)
        self.assertEqual(self.ledger.get_account_balance(self.user_addr, self.asset_3_id)[0], swap_2_amount_out)
