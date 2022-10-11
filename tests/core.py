import unittest
from decimal import Decimal

from algosdk.encoding import decode_address
from algosdk.future import transaction

from .constants import *
from .utils import get_pool_logicsig_bytecode


class BaseTestCase(unittest.TestCase):
    maxDiff = None

    def create_amm_app(self):
        if self.app_creator_address not in self.ledger.accounts:
            self.ledger.set_account_balance(self.app_creator_address, 1_000_000)

        self.ledger.create_app(
            app_id=APPLICATION_ID,
            approval_program=amm_approval_program,
            creator=self.app_creator_address,
            local_ints=APP_LOCAL_INTS,
            local_bytes=APP_LOCAL_BYTES,
            global_ints=APP_GLOBAL_INTS,
            global_bytes=APP_GLOBAL_BYTES
        )
        # 100_000 for basic min balance requirement
        # + 100_000 for increase_cost_budget app creation min balance requirement
        self.ledger.set_account_balance(APPLICATION_ADDRESS, 200_000)
        self.ledger.set_global_state(
            APPLICATION_ID,
            {
                b'fee_collector': decode_address(self.app_creator_address),
                b'fee_manager': decode_address(self.app_creator_address),
                b'fee_setter': decode_address(self.app_creator_address),
            }
        )

    def bootstrap_pool(self, asset_1_id, asset_2_id):
        lsig = get_pool_logicsig_bytecode(amm_pool_template, APPLICATION_ID, asset_1_id, asset_2_id)
        pool_address = lsig.address()

        if asset_2_id:
            minimum_balance = MIN_POOL_BALANCE_ASA_ASA_PAIR
        else:
            minimum_balance = MIN_POOL_BALANCE_ASA_ALGO_PAIR

        # Algojig cannot account application opt-in requirements right now.
        local_state_requirements = (25000 + 3500) * APP_LOCAL_INTS + (25000 + 25000) * APP_LOCAL_BYTES
        minimum_balance -= local_state_requirements

        # Set Algo balance (min balance + 100_000 to be transferred to the app account)
        self.ledger.set_account_balance(pool_address, minimum_balance + 100_000)

        # Rekey to application address
        self.ledger.set_auth_addr(pool_address, APPLICATION_ADDRESS)

        # Opt-in to assets
        self.ledger.set_account_balance(pool_address, 0, asset_id=asset_1_id)
        if asset_2_id != 0:
            self.ledger.set_account_balance(pool_address, 0, asset_id=asset_2_id)

        # Create pool token
        pool_token_asset_id = self.ledger.create_asset(asset_id=None, params=dict(creator=APPLICATION_ADDRESS))

        # Transfer Algo to application address
        self.ledger.move(100_000, asset_id=0, sender=pool_address, receiver=APPLICATION_ADDRESS)

        # Transfer pool tokens from application adress to pool
        self.ledger.set_account_balance(APPLICATION_ADDRESS, 0, asset_id=pool_token_asset_id)
        self.ledger.set_account_balance(pool_address, POOL_TOKEN_TOTAL_SUPPLY, asset_id=pool_token_asset_id)

        self.ledger.set_local_state(
            address=pool_address,
            app_id=APPLICATION_ID,
            state={
                b'asset_1_id': asset_1_id,
                b'asset_2_id': asset_2_id,
                b'pool_token_asset_id': pool_token_asset_id,

                b'total_fee_share': TOTAL_FEE_SHARE,
                b'protocol_fee_ratio': PROTOCOL_FEE_RATIO,

                b'asset_1_reserves': 0,
                b'asset_2_reserves': 0,
                b'issued_pool_tokens': 0,

                b'asset_1_cumulative_price': BYTE_ZERO,
                b'asset_2_cumulative_price': BYTE_ZERO,
                b'cumulative_price_update_timestamp': 0,

                b'lock': 0,

                b'asset_1_protocol_fees': 0,
                b'asset_2_protocol_fees': 0,
            }
        )
        self.assertEqual(self.ledger.get_account_balance(pool_address)[0], minimum_balance)
        return pool_address, pool_token_asset_id

    def set_initial_pool_liquidity(self, pool_address, asset_1_id, asset_2_id, pool_token_asset_id, asset_1_reserves, asset_2_reserves, liquidity_provider_address=None):
        issued_pool_token_amount = int(Decimal.sqrt(Decimal(asset_1_reserves) * Decimal(asset_2_reserves)))
        pool_token_out_amount = issued_pool_token_amount - LOCKED_POOL_TOKENS
        assert pool_token_out_amount > 0

        self.ledger.update_local_state(
            address=pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_reserves': asset_1_reserves,
                b'asset_2_reserves': asset_2_reserves,
                b'issued_pool_tokens': issued_pool_token_amount,
            }
        )

        self.ledger.move(sender=liquidity_provider_address, receiver=pool_address, amount=asset_1_reserves, asset_id=asset_1_id)
        self.ledger.move(sender=liquidity_provider_address, receiver=pool_address, amount=asset_2_reserves, asset_id=asset_2_id)
        self.ledger.move(sender=pool_address, receiver=liquidity_provider_address, amount=pool_token_out_amount, asset_id=pool_token_asset_id)

    def set_pool_protocol_fees(self, asset_1_protocol_fees, asset_2_protocol_fees):
        self.ledger.update_local_state(
            address=self.pool_address,
            app_id=APPLICATION_ID,
            state_delta={
                b'asset_1_protocol_fees': asset_1_protocol_fees,
                b'asset_2_protocol_fees': asset_2_protocol_fees,
            }
        )

        self.ledger.move(receiver=self.pool_address, amount=asset_1_protocol_fees, asset_id=self.asset_1_id)
        self.ledger.move(receiver=self.pool_address, amount=asset_2_protocol_fees, asset_id=self.asset_1_id)

    def get_add_initial_liquidity_transactions(self, asset_1_amount, asset_2_amount, app_call_fee=None):
        txn_group = []
        txn_group.append(
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_1_id,
                amt=asset_1_amount,
            )
        )
        txn_group.append(
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.asset_2_id,
                amt=asset_2_amount,
            ) if self.asset_2_id else transaction.PaymentTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                amt=asset_2_amount,
            )
        )
        txn_group.append(
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_ADD_INITIAL_LIQUIDITY],
                foreign_assets=[self.pool_token_asset_id],
                accounts=[self.pool_address],
            )
        )
        txn_group[-1].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_add_liquidity_transactions(self, asset_1_amount, asset_2_amount, min_output=0, app_call_fee=None):
        txn_group = []
        if asset_1_amount is not None and asset_2_amount is not None:
            mode = 'flexible'
        else:
            mode = 'single'
        if asset_1_amount is not None:
            txn_group.append(
                transaction.AssetTransferTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    receiver=self.pool_address,
                    index=self.asset_1_id,
                    amt=asset_1_amount,
                )
            )
        if asset_2_amount is not None:
            txn_group.append(
                transaction.AssetTransferTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    receiver=self.pool_address,
                    index=self.asset_2_id,
                    amt=asset_2_amount,
                ) if self.asset_2_id else transaction.PaymentTxn(
                    sender=self.user_addr,
                    sp=self.sp,
                    receiver=self.pool_address,
                    amt=asset_2_amount,
                )
            )
        txn_group.append(
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_ADD_LIQUIDITY, mode, min_output],
                foreign_assets=[self.pool_token_asset_id],
                accounts=[self.pool_address],
            )
        )
        txn_group[-1].fee = app_call_fee or (self.sp.fee * 3)
        return txn_group

    def get_remove_liquidity_transactions(self, liquidity_asset_amount, min_output_1=0, min_output_2=0, app_call_fee=None):
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.pool_token_asset_id,
                amt=liquidity_asset_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_REMOVE_LIQUIDITY, min_output_1, min_output_2],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_remove_liquidity_single_transactions(self, liquidity_asset_amount, asset_id, min_output=0, app_call_fee=None):
        min_output_1 = min_output if asset_id == self.asset_1_id else 0
        min_output_2 = min_output if asset_id == self.asset_1_id else 0
        txn_group = [
            transaction.AssetTransferTxn(
                sender=self.user_addr,
                sp=self.sp,
                receiver=self.pool_address,
                index=self.pool_token_asset_id,
                amt=liquidity_asset_amount,
            ),
            transaction.ApplicationNoOpTxn(
                sender=self.user_addr,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_REMOVE_LIQUIDITY, min_output_1, min_output_2],
                foreign_assets=[asset_id],
                accounts=[self.pool_address],
            )
        ]
        txn_group[1].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_claim_fee_transactions(self, sender, fee_collector, app_call_fee=None):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=sender,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_CLAIM_FEES],
                foreign_assets=[self.asset_1_id, self.asset_2_id],
                accounts=[self.pool_address, fee_collector],
            )
        ]
        txn_group[0].fee = app_call_fee or self.sp.fee
        return txn_group

    def get_claim_extra_transactions(self, sender, asset_id, address, fee_collector, app_call_fee=None):
        txn_group = [
            transaction.ApplicationNoOpTxn(
                sender=sender,
                sp=self.sp,
                index=APPLICATION_ID,
                app_args=[METHOD_CLAIM_EXTRA],
                foreign_assets=[asset_id],
                accounts=[address, fee_collector],
            )
        ]
        txn_group[0].fee = app_call_fee or self.sp.fee
        return txn_group

    @classmethod
    def sign_txns(cls, txns, secret_key):
        return [txn.sign(secret_key) for txn in txns]
