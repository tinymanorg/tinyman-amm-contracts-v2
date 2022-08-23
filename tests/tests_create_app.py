from unittest.mock import ANY

from algojig import get_suggested_params
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .core import BaseTestCase, amm_approval_program, amm_clear_state_program


class TestCreateApp(BaseTestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.app_creator_sk, cls.app_creator_address = generate_account()
        cls.user_sk, cls.user_addr = generate_account()

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

    def test_create_app(self):
        extra_pages = 2
        txn = transaction.ApplicationCreateTxn(
            sender=self.app_creator_address,
            sp=self.sp,
            on_complete=transaction.OnComplete.NoOpOC,
            approval_program=amm_approval_program.bytecode,
            clear_program=amm_clear_state_program.bytecode,
            global_schema=transaction.StateSchema(num_uints=1, num_byte_slices=3),
            local_schema=transaction.StateSchema(num_uints=11, num_byte_slices=0),
            extra_pages=extra_pages,
        )
        stxn = txn.sign(self.app_creator_sk)

        block = self.ledger.eval_transactions(transactions=[stxn])
        block_txns = block[b'txns']

        self.assertAlmostEqual(len(block_txns), 1)
        txn = block_txns[0]
        self.assertTrue(txn[b'apid'] > 0)
        self.assertDictEqual(
            txn[b'txn'],
            {
                b'apap': amm_approval_program.bytecode,
                b'apep': extra_pages,
                b'apgs': ANY,
                b'apls': ANY,
                b'apsu': amm_clear_state_program.bytecode,
                b'fee': self.sp.fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(self.app_creator_address),
                b'type': b'appl'
            }
        )

        self.assertDictEqual(
            txn[b'dt'][b'gd'],
            {
                b'fee_collector': {b'at': 1, b'bs': decode_address(self.app_creator_address)},
                b'fee_manager': {b'at': 1, b'bs': decode_address(self.app_creator_address)},
                b'fee_setter': {b'at': 1, b'bs': decode_address(self.app_creator_address)},
            }
        )
