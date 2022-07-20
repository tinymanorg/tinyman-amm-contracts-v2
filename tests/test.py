import unittest
import algosdk
import base64
from algosdk.logic import get_application_address
from algosdk.future import transaction
from algosdk.encoding import decode_address, encode_address
from algojig import TealishProgram, sp
from algojig.ledger import JigLedger
from algojig.exceptions import LogicEvalError, LogicSigReject


addr = 'RTR6MP4VKLZRBLKTNWR4PDH5QGMQYVDRQ6OSBEYR6OJLK7W2YKY2HFGKLE'
sk = 'vJB2vFVww2xs7fvfZcr8LQTWkGO5MEwS+jwfRfzcoZeM4+Y/lVLzEK1TbaPHjP2BmQxUcYedIJMR85K1ftrCsQ=='

logicsig = TealishProgram('contracts/pool_template.tl')
approval_program = TealishProgram('contracts/amm_approval.tl')

application_id = 1

def get_pool_logicsig_bytecode(asset_1_id, asset_2_id):
    fee_tier = 3
    # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
    template = b'\x06\x80 \x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x01\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
    program = bytearray(template)
    program[3:11] = (application_id).to_bytes(8, 'big')
    program[11:19] = asset_1_id.to_bytes(8, 'big')
    program[19:27] = asset_2_id.to_bytes(8, 'big')
    program[27:35] = fee_tier.to_bytes(8, 'big')
    return transaction.LogicSigAccount(program)



lsig = get_pool_logicsig_bytecode(5, 2)
pool_address = lsig.address()
print('Pool Address:', pool_address)

app_address = get_application_address(application_id)
print('App Address:', app_address)


class TestBootstrap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pass

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=application_id, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.set_account_balance(addr, 0, asset_id=2)
        self.ledger.set_account_balance(addr, 0, asset_id=5)

    def test_pass(self):
        lsig = get_pool_logicsig_bytecode(5, 2)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(), sp=sp, index=application_id,
                    app_args=["bootstrap", 5, 2, 3],
                    foreign_assets=[5, 2],
                    rekey_to=app_address,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = 7000

        block = self.ledger.eval_transactions(transactions)
        txns = block[b'txns']
        # print(txns[0][b'dt'][b'itx'])

        pool_delta = txns[0][b'dt'][b'ld'][0]
        self.assertDictEqual(pool_delta[b'asset_1_id'], {b'at': 2, b'ui': 5})
        self.assertDictEqual(pool_delta[b'asset_2_id'], {b'at': 2, b'ui': 2})

    def test_fail_wrong_ids_for_logicsig(self):
        lsig = get_pool_logicsig_bytecode(5, 2)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(), sp=sp, index=application_id,
                    app_args=["bootstrap", 4, 2, 3],
                    foreign_assets=[4, 2],
                    rekey_to=app_address,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = 7000
        with self.assertRaises(LogicEvalError):
            block = self.ledger.eval_transactions(transactions)

    def test_fail_wrong_asset_order(self):
        lsig = get_pool_logicsig_bytecode(2, 5)
        pool_address = lsig.address()
        self.ledger.set_account_balance(pool_address, 2_000_000)
        transactions = [
            transaction.LogicSigTransaction(
                transaction.ApplicationOptInTxn(
                    sender=lsig.address(), sp=sp, index=application_id,
                    app_args=["bootstrap", 2, 5, 3],
                    foreign_assets=[2, 5],
                    rekey_to=app_address,
                ),
                lsig
            )
        ]
        transactions[0].transaction.fee = 7000
        with self.assertRaises(LogicEvalError):
            block = self.ledger.eval_transactions(transactions)



class TestSwap(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pass

    def setUp(self):
        self.ledger = JigLedger()
        self.ledger.create_app(app_id=application_id, approval_program=approval_program)
        self.ledger.set_account_balance(addr, 1_000_000)
        self.ledger.set_account_balance(addr, 0, asset_id=2)
        self.ledger.set_account_balance(addr, 1_000_000, asset_id=5)
        lsig = get_pool_logicsig_bytecode(5, 2)
        self.pool_address = lsig.address()
        self.ledger.set_account_balance(self.pool_address, 1_000_000)
        self.ledger.set_auth_addr(self.pool_address, app_address)

    def test_fixed_input_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
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
        self.assertEqual(itxn0[b'snd'], decode_address(pool_address))


    def test_fixed_output_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
                app_args=["swap", 5, 2, 9872, "fixed-output"],
                foreign_assets=[5, 2],
                accounts=[pool_address],
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
        self.assertEqual(itxn0[b'snd'], decode_address(pool_address))

    def test_fixed_output_with_change_pass(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=pool_address,
                index=5,
                amt=10_100,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
                app_args=["swap", 5, 2, 9872, "fixed-output"],
                foreign_assets=[5, 2],
                accounts=[pool_address],
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
        self.assertEqual(itxn0[b'snd'], decode_address(pool_address))

        # Check details of output inner transaction
        itxn1 = txns[1][b'dt'][b'itx'][1][b'txn']
        self.assertEqual(itxn1[b'aamt'], 9872)
        self.assertEqual(itxn1[b'arcv'], decode_address(addr))
        self.assertEqual(itxn1[b'xaid'], 2)
        self.assertEqual(itxn1[b'snd'], decode_address(pool_address))

    def test_fail_insufficient_fee(self):
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
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
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=self.pool_address,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
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
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
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
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=5)
        self.ledger.set_account_balance(self.pool_address, 1_000_000, asset_id=2)
        self.ledger.set_local_state(self.pool_address, application_id, {
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
                sp=sp,
                receiver=self.pool_address,
                index=5,
                amt=10_000,
            ),
            transaction.ApplicationNoOpTxn(
                sender=addr, sp=sp, index=application_id,
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