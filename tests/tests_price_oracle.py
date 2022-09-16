from datetime import datetime, timedelta
from unittest.mock import ANY
from zoneinfo import ZoneInfo

from algojig import get_suggested_params
from algojig.ledger import JigLedger
from algosdk.account import generate_account
from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .core import BaseTestCase
from .utils import get_pool_logicsig_bytecode, int_to_bytes_without_zero_padding

price_oracle_reader_program = TealishProgram('tests/price_oracle_reader.tl')
PRICE_ORACLE_READER_APP_ID = 10


class TestPriceOracle(BaseTestCase):

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

    def test_overflow(self):
        bootstrap_datetime = datetime(year=2022, month=1, day=1, tzinfo=ZoneInfo("UTC"))
        two_hundred_years_later = datetime(year=2222, month=1, day=1, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(two_hundred_years_later.year, 2222)

        # Maximum possible price
        asset_1_reserves = 1
        asset_2_reserves = MAX_ASSET_AMOUNT

        self.set_initial_pool_liquidity(asset_1_reserves, asset_2_reserves)
        self.ledger.update_local_state(address=self.pool_address, app_id=APPLICATION_ID, state_delta={b'cumulative_price_update_timestamp': int(bootstrap_datetime.timestamp())})

        min_output = 0
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=334,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, "fixed-input", min_output],
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
        block = self.ledger.eval_transactions(stxns, block_timestamp=int(two_hundred_years_later.timestamp()))
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 2)

        # outer transactions - [1]
        txn = block_txns[1]
        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        asset_1_cumulative_price = asset_2_reserves * PRICE_SCALE_FACTOR * (int(two_hundred_years_later.timestamp()) - int(bootstrap_datetime.timestamp())) // asset_1_reserves
        asset_2_cumulative_price = asset_1_reserves * PRICE_SCALE_FACTOR * (int(two_hundred_years_later.timestamp()) - int(bootstrap_datetime.timestamp())) // asset_2_reserves

        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'asset_1_reserves': {b'at': 2, b'ui': 335},
                b'asset_2_reserves': {b'at': 2, b'ui': 55229772675777101},
                b'asset_1_cumulative_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(asset_1_cumulative_price)},
                b'asset_2_cumulative_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(asset_2_cumulative_price)},
                b'cumulative_price_update_timestamp': {b'at': 2, b'ui': int(two_hundred_years_later.timestamp())},
            }
        )
        self.assertEqual(asset_1_cumulative_price, 2147640163675837592635447824606866120216936448000)
        self.assertEqual(asset_2_cumulative_price, 6311347200)

        time_delta = int(two_hundred_years_later.timestamp()) - int(bootstrap_datetime.timestamp())
        self.assertEqual(asset_2_reserves / asset_1_reserves, asset_1_cumulative_price / time_delta / PRICE_SCALE_FACTOR)
        self.assertEqual(asset_1_reserves / asset_2_reserves, asset_2_cumulative_price / time_delta / PRICE_SCALE_FACTOR)

    def test_updated_once_in_a_block(self):
        one_day = timedelta(days=1)
        bootstrap_datetime = datetime(year=2022, month=1, day=1, tzinfo=ZoneInfo("UTC"))
        last_update_datetime = bootstrap_datetime + one_day
        new_block_datetime = last_update_datetime + one_day

        # Random initial cumulative prices
        asset_1_cumulative_price = 2 * PRICE_SCALE_FACTOR * int(timedelta(days=7).total_seconds())
        asset_2_cumulative_price = 3 * PRICE_SCALE_FACTOR * int(timedelta(days=7).total_seconds())

        asset_1_reserves = 12_345
        asset_2_reserves = 29_876
        self.set_initial_pool_liquidity(asset_1_reserves, asset_2_reserves)
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_cumulative_price': int_to_bytes_without_zero_padding(asset_1_cumulative_price),
                b'asset_2_cumulative_price': int_to_bytes_without_zero_padding(asset_2_cumulative_price),
                b'cumulative_price_update_timestamp': int(last_update_datetime.timestamp())
            }
        )

        new_asset_1_cumulative_price = asset_1_cumulative_price + (asset_2_reserves * PRICE_SCALE_FACTOR * int(one_day.total_seconds()) // asset_1_reserves)
        new_asset_2_cumulative_price = asset_2_cumulative_price + (asset_1_reserves * PRICE_SCALE_FACTOR * int(one_day.total_seconds()) // asset_2_reserves)

        min_output = 0
        txn_group_1 = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=334,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, "fixed-input", min_output],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group_1[1].fee = 2000
        txn_group_1 = transaction.assign_group_id(txn_group_1)

        txn_group_2 = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=334,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, "fixed-input", min_output],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group_2[1].fee = 2000
        txn_group_2 = transaction.assign_group_id(txn_group_2)

        stxns = [
            txn_group_1[0].sign(self.user_sk),
            txn_group_1[1].sign(self.user_sk),
            txn_group_2[0].sign(self.user_sk),
            txn_group_2[1].sign(self.user_sk),
        ]
        block = self.ledger.eval_transactions(stxns, block_timestamp=int(new_block_datetime.timestamp()))
        block_txns = block[b'txns']

        # outer transactions
        self.assertEqual(len(block_txns), 4)

        # outer transactions - [1]
        txn = block_txns[1]
        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'asset_1_reserves': ANY,
                b'asset_2_reserves': ANY,
                b'asset_1_cumulative_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(new_asset_1_cumulative_price)},
                b'asset_2_cumulative_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(new_asset_2_cumulative_price)},
                b'cumulative_price_update_timestamp': {b'at': 2, b'ui': int(new_block_datetime.timestamp())},
            }
        )

        # Cumulative prices are not updated with second swap
        # outer transactions - [3]
        txn = block_txns[3]
        # local state delta
        pool_local_state_delta = txn[b'dt'][b'ld'][1]
        self.assertDictEqual(
            pool_local_state_delta,
            {
                b'asset_1_reserves': ANY,
                b'asset_2_reserves': ANY,
            }
        )

    def test_read_price(self):
        """
        A dummy app reads the local state of the pool and calculates the price.
        """
        self.ledger.create_app(app_id=PRICE_ORACLE_READER_APP_ID, approval_program=price_oracle_reader_program)
        asset_1_reserves = asset_2_reserves = 1_000_000
        self.set_initial_pool_liquidity(asset_1_reserves, asset_2_reserves)

        byte_pool_address = decode_address(self.pool_address)

        min_output = 0
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=334,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_SWAP, "fixed-input", min_output],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = 2000

        txn_group = transaction.assign_group_id(txn_group)
        stxns = [
            txn_group[0].sign(self.user_sk),
            txn_group[1].sign(self.user_sk),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=PRICE_ORACLE_READER_APP_ID,
                foreign_apps=[APPLICATION_ID],
                accounts=[self.pool_address],
            ).sign(self.user_sk)
        ]

        block = self.ledger.eval_transactions(stxns, block_timestamp=2000)
        block_txns = block[b'txns']
        self.assertDictEqual(
            block_txns[2][b'dt'][b'gd'],
            {
                byte_pool_address + b'_asset_1_cumulative_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(1 * 2000 * PRICE_SCALE_FACTOR)},
                byte_pool_address + b'_asset_2_cumulative_price': {b'at': 1, b'bs': int_to_bytes_without_zero_padding(1 * 2000 * PRICE_SCALE_FACTOR)},
                byte_pool_address + b'_price_update_timestamp': {b'at': 2, b'ui': 2000}
            }
        )

        block = self.ledger.eval_transactions(stxns, block_timestamp=3000)
        block_txns = block[b'txns']
        self.assertDictEqual(
            block_txns[2][b'dt'][b'gd'],
            {
                byte_pool_address + b'_asset_1_price': {b'at': 1, b'bs': ANY},
                byte_pool_address + b'_asset_2_price': {b'at': 1, b'bs': ANY},
                byte_pool_address + b'_asset_1_cumulative_price': {b'at': 1, b'bs': ANY},
                byte_pool_address + b'_asset_2_cumulative_price': {b'at': 1, b'bs': ANY},
                byte_pool_address + b'_price_update_timestamp': {b'at': 2, b'ui': 3000}
            }
        )
        self.assertEqual(int.from_bytes(block_txns[2][b'dt'][b'gd'][byte_pool_address + b'_asset_1_price'][b'bs'], "big"), 18434462644153932631)
        self.assertEqual(int.from_bytes(block_txns[2][b'dt'][b'gd'][byte_pool_address + b'_asset_2_price'][b'bs'], "big"), 18459033685413727963)
        self.assertAlmostEqual(int.from_bytes(block_txns[2][b'dt'][b'gd'][byte_pool_address + b'_asset_1_price'][b'bs'], "big") / PRICE_SCALE_FACTOR, 0.9999, delta=0.001)
        self.assertAlmostEqual(int.from_bytes(block_txns[2][b'dt'][b'gd'][byte_pool_address + b'_asset_2_price'][b'bs'], "big") / PRICE_SCALE_FACTOR, 1.0000, delta=0.001)
        self.assertEqual(int.from_bytes(block_txns[2][b'dt'][b'gd'][byte_pool_address + b'_asset_1_cumulative_price'][b'bs'], "big"), 55327950791573035863364)
        self.assertEqual(int.from_bytes(block_txns[2][b'dt'][b'gd'][byte_pool_address + b'_asset_2_cumulative_price'][b'bs'], "big"), 55352521832832831195923)
