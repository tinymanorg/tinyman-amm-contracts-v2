import unittest

from algojig import TealishProgram, get_suggested_params
from algojig.exceptions import LogicEvalError
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction
from algosdk.logic import get_application_address

addr = 'RTR6MP4VKLZRBLKTNWR4PDH5QGMQYVDRQ6OSBEYR6OJLK7W2YKY2HFGKLE'
sk = 'vJB2vFVww2xs7fvfZcr8LQTWkGO5MEwS+jwfRfzcoZeM4+Y/lVLzEK1TbaPHjP2BmQxUcYedIJMR85K1ftrCsQ=='

logicsig = TealishProgram('contracts/pool_template.tl')
approval_program = TealishProgram('contracts/amm_approval.tl')

ALGO_ASSET_ID = 0
APPLICATION_ID = 1
APPLICATION_ADDRESS = get_application_address(APPLICATION_ID)
print('App Address:', APPLICATION_ADDRESS)


def get_pool_logicsig_bytecode(asset_1_id, asset_2_id):
    fee_tier = 3
    # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
    template = b'\x06\x80 \x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
    program = bytearray(logicsig.bytecode)

    # Algo SDK doesn't support teal version 6 at the moment
    program[0:1] = (6).to_bytes(1, "big")
    assert program == bytearray(template)

    program[3:11] = (APPLICATION_ID).to_bytes(8, 'big')
    program[11:19] = asset_1_id.to_bytes(8, 'big')
    program[19:27] = asset_2_id.to_bytes(8, 'big')
    program[27:35] = fee_tier.to_bytes(8, 'big')
    return transaction.LogicSigAccount(program)


# lsig = get_pool_logicsig_bytecode(5, 2)
# pool_address = lsig.address()
# print('Pool Address:', pool_address)


class TestBootstrap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.minimum_fee = 7000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.asset_2_id = 2
        cls.fee_tier = 3
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USD"))
        self.ledger.create_asset(self.asset_2_id, params=dict(unit_name="BTC"))
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_1_id)
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_2_id)

    def test_pass(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
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
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), self.asset_2_id.to_bytes(8, "big"), self.fee_tier.to_bytes(8, "big")],
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

        # inner-transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 6)

        # inner-transactions - [0]
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

        # inner-transactions - [1]
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

        # inner-transactions - [2]
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

        # inner-transactions - [3]
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

        # inner-transactions - [4]
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

        # inner-transactions - [5]
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

        # local delta
        pool_delta = txn[b'dt'][b'ld'][0]
        self.assertDictEqual(
            pool_delta,
            {
                b'asset_1_id': {b'at': 2, b'ui': self.asset_1_id},
                b'asset_2_id': {b'at': 2, b'ui': self.asset_2_id},
                b'fee_tier': {b'at': 2, b'ui': self.fee_tier},
                b'pool_token_id': {b'at': 2, b'ui': created_asset_id},
                b'poolers_fee_share': {b'at': 2, b'ui': 25},
                b'protocol_fee_share': {b'at': 2, b'ui': 5}
            }
        )

    def test_fail_rekey(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)

        # TODO: Isn't this transaction rejected by the pool logic sig?
        # Rekey is missing
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(Txn.RekeyTo == Global.CurrentApplicationAddress)')

        # Rekey address is wrong
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=generate_account()[1],
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(Txn.RekeyTo == Global.CurrentApplicationAddress)')

    def test_fail_wrong_ids_for_logicsig(self):
        wrong_asset_1_id = self.asset_1_id + 1
        lsig = get_pool_logicsig_bytecode(wrong_asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(hash == Txn.Sender)')

    def test_fail_wrong_asset_order(self):
        lsig = get_pool_logicsig_bytecode(self.asset_2_id, self.asset_1_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_2_id, self.asset_1_id, self.fee_tier],
                    foreign_assets=[self.asset_2_id, self.asset_1_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_id > asset_2_id)')

    def test_fail_different_assets_are_included_in_application_args_and_foreign_assets(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id + 9999, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(asset_1_id == Txn.Assets[0])')

        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id + 9999],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(asset_2_id == Txn.Assets[1])')

    def test_fail_insufficient_fee(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = self.minimum_fee - 1

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'inner_txn:')

    def test_fail_wrong_method_name(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, self.asset_2_id)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["invalid", self.asset_1_id, self.asset_2_id, self.fee_tier],
                    foreign_assets=[self.asset_1_id, self.asset_2_id],
                    rekey_to=APPLICATION_ADDRESS,
                ),
                lsig
            )
        ]

        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(transactions)
        self.assertEqual(e.exception.source['line'], 'assert(Txn.ApplicationArgs[0] == "bootstrap")')


class TestBootstrapAlgoPair(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()
        cls.minimum_fee = 6000
        cls.sp.fee = cls.minimum_fee
        cls.asset_1_id = 5
        cls.fee_tier = 3
        cls.pool_token_total_supply = 18446744073709551615

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.create_asset(self.asset_1_id, params=dict(unit_name="USD"))
        self.ledger.set_account_balance(addr, 0, asset_id=self.asset_1_id)

    def test_pass(self):
        lsig = get_pool_logicsig_bytecode(self.asset_1_id, ALGO_ASSET_ID)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(),
                    sp=self.sp,
                    index=APPLICATION_ID,
                    app_args=["bootstrap", self.asset_1_id, ALGO_ASSET_ID, self.fee_tier],
                    foreign_assets=[self.asset_1_id],
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
                b'apaa': [b'bootstrap', self.asset_1_id.to_bytes(8, "big"), ALGO_ASSET_ID.to_bytes(8, "big"), self.fee_tier.to_bytes(8, "big")],
                b'apan': transaction.OnComplete.OptInOC,
                b'apas': [self.asset_1_id],
                b'apid': APPLICATION_ID,
                b'fee': self.minimum_fee,
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'rekey': decode_address(APPLICATION_ADDRESS),
                b'snd': decode_address(pool_address),
                b'type': b'appl'
            }
        )

        # inner-transactions
        inner_transactions = txn[b'dt'][b'itx']
        self.assertEqual(len(inner_transactions), 5)

        # inner-transactions - [0]
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

        # inner-transactions - [1]
        created_asset_id = inner_transactions[1][b'caid']
        self.assertDictEqual(
            inner_transactions[1][b'txn'],
            {
                b'apar': {
                    b'an': b'TinymanPool2.0 USD-ALGO',
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

        # inner-transactions - [2]
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

        # inner-transactions - [3]
        self.assertDictEqual(
            inner_transactions[3][b'txn'],
            {
                b'arcv': decode_address(pool_address),
                b'fv': self.sp.first,
                b'lv': self.sp.last,
                b'snd': decode_address(pool_address),
                b'type': b'axfer',
                b'xaid': created_asset_id
            }
        )

        # inner-transactions - [4]
        self.assertDictEqual(
            inner_transactions[4][b'txn'],
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

        # local delta
        pool_delta = txn[b'dt'][b'ld'][0]
        self.assertDictEqual(
            pool_delta,
            {
                b'asset_1_id': {b'at': 2, b'ui': self.asset_1_id},
                b'asset_2_id': {b'at': 2},      # b'ui': ALGO_ASSET_ID
                b'fee_tier': {b'at': 2, b'ui': self.fee_tier},
                b'pool_token_id': {b'at': 2, b'ui': created_asset_id},
                b'poolers_fee_share': {b'at': 2, b'ui': 25},
                b'protocol_fee_share': {b'at': 2, b'ui': 5}
            }
        )


class TestSwap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sp = get_suggested_params()

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=APPLICATION_ID,
                               approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.set_account_balance(addr, 0, asset_id=2)
        self.ledger.set_account_balance(addr, 1_000_000, asset_id=5)
        lsig = get_pool_logicsig_bytecode(5, 2)
        self.pool_address = lsig.address()
        self.ledger.set_account_balance(self.pool_address, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address, APPLICATION_ADDRESS)

    def test_fixed_input_pass(self):
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 2, 9000, "fixed-input"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 9872)
        self.assertEqual(itxn0[b'arcv'], decode_address(addr))
        self.assertEqual(itxn0[b'xaid'], 2)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

    def test_fixed_output_pass(self):
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 2, 9872, "fixed-output"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 3000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 1)

        # Check details of output inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 9872)
        self.assertEqual(itxn0[b'arcv'], decode_address(addr))
        self.assertEqual(itxn0[b'xaid'], 2)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

    def test_fixed_output_with_change_pass(self):
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=5,
                amt=10_100,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 2, 9872, "fixed-output"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 3000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        block = self.ledger.eval_transactions(stxns)
        txns = block[b'txns']
        self.assertEqual(len(txns[1][b'dt'][b'itx']), 2)

        # Check details of input change inner transaction
        itxn0 = txns[1][b'dt'][b'itx'][0][b'txn']
        self.assertEqual(itxn0[b'aamt'], 100)
        self.assertEqual(itxn0[b'arcv'], decode_address(addr))
        self.assertEqual(itxn0[b'xaid'], 5)
        self.assertEqual(itxn0[b'snd'], decode_address(self.pool_address))

        # Check details of output inner transaction
        itxn1 = txns[1][b'dt'][b'itx'][1][b'txn']
        self.assertEqual(itxn1[b'aamt'], 9872)
        self.assertEqual(itxn1[b'arcv'], decode_address(addr))
        self.assertEqual(itxn1[b'xaid'], 2)
        self.assertEqual(itxn1[b'snd'], decode_address(self.pool_address))

    def test_fail_insufficient_fee(self):
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 2, 9000, "fixed-input"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 1000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('fee too small', e.exception.error)

    def test_fail_wrong_asset_in(self):
        self.ledger.set_account_balance(addr, 1_000_000, asset_id=0)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.PaymentTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 2, 9000, "fixed-input"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('assert failed', e.exception.error)

    def test_fail_wrong_asset_out_1(self):
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 0, 9000, "fixed-input"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)

    def test_fail_wrong_asset_out_2(self):
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(
            self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, APPLICATION_ID, {
            b'asset_1_id': 5,
            b'asset_2_id': 2,
            b'asset_1_reserves': 1_000_000,
            b'asset_2_reserves': 1_000_000,
            b'poolers_fee_share': 25,
            b'protocol_fee_share': 5,
        })
        txn_group = [
            transaction.AssetTransferTxn(
                sender=addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=["swap", 5, 5, 9000, "fixed-input"],
                foreign_assets=[5, 2],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000
        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(sk),
            txn_group[1].sign(sk)
        ]
        with self.assertRaises(LogicEvalError) as e:
            self.ledger.eval_transactions(stxns)
        self.assertIn('err opcode executed', e.exception.error)


if __name__ == '__main__':
    unittest.main()
