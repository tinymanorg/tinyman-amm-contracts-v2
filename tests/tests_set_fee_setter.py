from algojig import get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode


class TestSetFeeSetter(BaseTestCase):
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

        lsig = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, self.asset_1_id, self.asset_2_id)
        self.pool_address = lsig.address()
        self.bootstrap_pool()
        self.ledger.opt_in_asset(self.user_addr, self.pool_token_asset_id)

    def test_pass(self):
        fee_manager_sk, fee_manager = self.app_creator_sk, self.app_creator_address
        _, fee_setter_1 = generate_account()
        _, fee_setter_2 = generate_account()
        self.ledger.set_account_balance(fee_manager, 1_000_000)
        self.ledger.set_account_balance(fee_setter_1, 1_000_000)
        self.ledger.set_account_balance(fee_setter_2, 1_000_000)

        # Group is not required.
        # Creator sets fee_setter to fee_setter_1
        # fee_setter_1 sets fee_setter to fee_setter_2
        txns = [
            transaction.ApplicationNoOpTxn(
                sender=fee_manager,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_SETTER],
                accounts=[fee_setter_1],
            ),
            transaction.ApplicationNoOpTxn(
                sender=fee_manager,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SET_FEE_SETTER],
                accounts=[fee_setter_2],
            )
        ]
        stxns = [
            txns[0].sign(fee_manager_sk),
            txns[1].sign(fee_manager_sk)
        ]

        block = self.ledger.eval_transactions(stxns)
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions[0]
        txn = block_txns[0]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_setter'],
                b'apat': [decode_address(fee_setter_1)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager),
                b'type': b'appl'
            }
        )
        # outer transactions[0] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_setter': {b'at': 1, b'bs': decode_address(fee_setter_1)}
            }
        )

        # outer transactions[1]
        txn = block_txns[1]
        # there is no inner transaction
        self.assertIsNone(txn[b'dt'].get(b'itx'))
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apaa': [b'set_fee_setter'],
                b'apat': [decode_address(fee_setter_2)],
                b'apid': APPLICATION_ID,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(fee_manager),
                b'type': b'appl'
            }
        )

        # outer transactions[1] - Global Delta
        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_setter': {b'at': 1, b'bs': decode_address(fee_setter_2)}
            }
        )

    def test_fail_sender_is_not_fee_setter(self):
        invalid_account_sk, invalid_account_address = generate_account()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)
        self.ledger.set_account_balance(invalid_account_address, 1_000_000)

        stxn = transaction.ApplicationNoOpTxn(
            sender=invalid_account_address,
            sp=self.sp,
            index=APPLICATION_ID,
            app_args=[METHOD_SET_FEE_SETTER],
            accounts=[invalid_account_address],
        ).sign(invalid_account_sk)

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions([stxn])
        self.assertEqual(e.exception.source['line'], 'assert(user_address == app_global_get("fee_manager"))')
