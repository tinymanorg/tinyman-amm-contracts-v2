from algojig import get_suggested_params
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode

proxy_approval_program = TealishProgram('tests/proxy_approval_program.tl')
PROXY_APP_ID = 10
PROXY_ADDRESS = get_application_address(PROXY_APP_ID)


class TestProxySwap(BaseTestCase):

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

        self.ledger.create_app(app_id=PROXY_APP_ID, approval_program=proxy_approval_program, creator=self.app_creator_address)
        self.ledger.set_account_balance(PROXY_ADDRESS, 1_000_000)
        self.ledger.set_account_balance(PROXY_ADDRESS, 0, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(PROXY_ADDRESS, 0, asset_id=self.asset_2_id)

        lsig = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.pool_token_asset_id = self.bootstrap_pool(self.asset_1_id, self.asset_2_id)

    def test_pass(self):
        self.set_initial_pool_liquidity(self.pool_address, self.asset_1_id, self.asset_2_id, self.pool_token_asset_id, asset_1_reserves=1_000_000, asset_2_reserves=1_000_000)

        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=PROXY_ADDRESS,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=PROXY_APP_ID,
                app_args=[METHOD_SWAP, 9000],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                foreign_apps=[APPLICATION_ID],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 5000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk),
        ]

        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        itxn = txns[1][b'dt'][b'itx'][-1][b'txn']
        self.assertEqual(itxn[b'aamt'], 9774)
        self.assertEqual(itxn[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn[b'snd'], decode_address(PROXY_ADDRESS))

        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 100)

        # do the same swap again and watch the fees accumulate
        self.ledger.eval_transactions(stxns)
        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 200)
