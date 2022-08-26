from algojig import get_suggested_params, LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode

proxy_approval_program = TealishProgram(tealish="""
    #pragma version 7

    const int TINYMAN_APP_ID = 1
    const int FEE_BASIS_POINTS = 100

    assert(Gtxn[0].AssetReceiver == Global.CurrentApplicationAddress)
    int swap_amount = (Gtxn[0].AssetAmount * (10000 - FEE_BASIS_POINTS)) / 10000
    int initial_output_balance
    _, initial_output_balance = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, Txn.Assets[1])
    inner_group:
        inner_txn:
            TypeEnum: Axfer
            Fee: 0
            AssetReceiver: Txn.Accounts[1]
            XferAsset: Gtxn[0].XferAsset
            AssetAmount: swap_amount
        end
        inner_txn:
            TypeEnum: Appl
            Fee: 0
            ApplicationID: TINYMAN_APP_ID
            ApplicationArgs[0]: "swap"
            ApplicationArgs[1]: Txn.ApplicationArgs[1]
            ApplicationArgs[2]: Txn.ApplicationArgs[2]
            ApplicationArgs[3]: Txn.ApplicationArgs[3]
            ApplicationArgs[4]: "fixed-input"
            Accounts[0]: Txn.Accounts[1]
            Assets[0]: Txn.Assets[0]
            Assets[1]: Txn.Assets[1]
        end
    end

    int new_output_balance
    _, new_output_balance = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, Txn.Assets[1])
    int output_amount = new_output_balance - initial_output_balance
    inner_txn:
        TypeEnum: Axfer
        Fee: 0
        AssetReceiver: Txn.Sender
        XferAsset: Txn.Assets[1]
        AssetAmount: output_amount
    end
    exit(1)
""")
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
        self.bootstrap_pool()

    def test_pass(self):
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
                receiver=PROXY_ADDRESS,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=PROXY_APP_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9000],
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
        self.assertEqual(itxn[b'aamt'], 9775)
        self.assertEqual(itxn[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn[b'snd'], decode_address(PROXY_ADDRESS))

        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 100)

        # do the same swap again and watch the fees accumulate
        self.ledger.eval_transactions(stxns)
        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 200)


class TestPoolSpecificToProxyApp(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()

        cls.minimum_fee = 7000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.create_amm_app()
        self.asset_2_id = self.ledger.create_asset(asset_id=None, params=dict(unit_name="BTC"))
        self.asset_1_id = self.ledger.create_asset(asset_id=None, params=dict(unit_name="USD"))
        self.ledger.set_account_balance(self.user_addr, 1_000_000)
        self.ledger.set_account_balance(self.user_addr, 1_000_000, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(self.user_addr, 0, asset_id=self.asset_2_id)
        self.pool_address = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id).address()

        self.ledger.create_app(app_id=PROXY_APP_ID, approval_program=proxy_approval_program, creator=self.app_creator_address)
        self.ledger.set_account_balance(PROXY_ADDRESS, 1_000_000)
        self.ledger.set_account_balance(PROXY_ADDRESS, 0, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(PROXY_ADDRESS, 0, asset_id=self.asset_2_id)

    def test_bootstrap(self):
        lsig = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id, proxy_app_id=PROXY_APP_ID)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=[METHOD_BOOTSTRAP, self.asset_1_id, self.asset_2_id, PROXY_APP_ID],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        block = self.ledger.eval_transactions(transactions)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertEqual(
            txn[b'txn'],
            {
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), self.asset_2_id.to_bytes(8, "big"), PROXY_APP_ID.to_bytes(8, "big")],
                b'apan': transaction.OnComplete.OptInOC,
                b'apas': [self.asset_1_id, self.asset_2_id],
                b'apid': APPLICATION_ID,
                b'fee': self.minimum_fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rekey': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'appl'
            }
        )

        # inner transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 6)

        # inner transactions - [0]
        self.assertDictEqual(
            inner_transactions[0][b'txn'],
            {
                b'amt': 200000,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rcv': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'pay'
            }
        )

        # inner transactions - [1]
        created_asset_id = inner_transactions[1][b'caid']

        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'apar': {
                    b'an': b'TinymanPool2.0 USD-BTC',
                    b'au': b'https://tinyman.org',
                    b'dc': 6,
                    b't': self.pool_token_total_supply,
                    b'un': b'TMPOOL2'
                },
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'acfg'
            }
        )

        # inner transactions - [2]
        self.assertDictEqual(
            inner_transactions[2][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_1_id
            }
        )

        # inner transactions - [3]
        self.assertDictEqual(
            inner_transactions[3][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': self.asset_2_id
            }
        )

        # inner transactions - [4]
        self.assertDictEqual(
            inner_transactions[4][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # inner transactions - [5]
        self.assertDictEqual(
            inner_transactions[5][b'txn'],
            {
                b'aamt': 18446744073709551615,
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(APPLICATION_ADDRESS),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # local state delta
        pool_delta = txn[b'dt'][b'ld'][0]
        self.assertDictEqual(
            pool_delta,
            {
                b'asset_1_id': {b'at': 2, b'ui': self.asset_1_id},
                b'asset_1_reserves': {b'at': 2},
                b'asset_2_id': {b'at': 2, b'ui': self.asset_2_id},
                b'asset_2_reserves': {b'at': 2},
                b'cumulative_asset_1_price': {b'at': 1, b'bs': BYTE_ZERO},
                b'cumulative_asset_2_price': {b'at': 1, b'bs': BYTE_ZERO},
                b'cumulative_price_update_timestamp': {b'at': 2, b'ui': BLOCK_TIME_DELTA},
                b'issued_pool_tokens': {b'at': 2},
                b'pool_token_asset_id': {b'at': 2, b'ui': created_asset_id},
                b'poolers_fee_share': {b'at': 2, b'ui': POOLERS_FEE_SHARE},
                b'protocol_fee_share': {b'at': 2, b'ui': PROTOCOL_FEE_SHARE},
                b'protocol_fees_asset_1': {b'at': 2},
                b'protocol_fees_asset_2': {b'at': 2},
                b'proxy_app_id': {b'at': 2, b'ui': PROXY_APP_ID},
            }
        )

    def test_swap_pass(self):
        self.bootstrap_pool(proxy_app_id=PROXY_APP_ID)
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
                receiver=PROXY_ADDRESS,
                index=self.asset_1_id,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=PROXY_APP_ID,
                app_args=[METHOD_SWAP, self.asset_1_id, self.asset_2_id, 9000],
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
        self.assertEqual(itxn[b'aamt'], 9775)
        self.assertEqual(itxn[b'arcv'], decode_address(self.user_addr))
        self.assertEqual(itxn[b'xaid'], self.asset_2_id)
        self.assertEqual(itxn[b'snd'], decode_address(PROXY_ADDRESS))

        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 100)

        # do the same swap again and watch the fees accumulate
        self.ledger.eval_transactions(stxns)
        self.assertEqual(self.ledger.get_account_balance(PROXY_ADDRESS, self.asset_1_id)[0], 200)

    def test_swap_fail(self):
        self.bootstrap_pool(proxy_app_id=PROXY_APP_ID)
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
        stxns = self.sign_txns(txn_group, self.user_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertEqual(e.exception.source['line'], 'assert(Global.CallerApplicationID == proxy_app_id)')
