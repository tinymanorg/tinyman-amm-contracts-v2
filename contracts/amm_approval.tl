#pragma version 7

# NOTE: Tinyman assumes that the asset id of ALGO is 0. Algorand cannot have an ASA with id 0.

# NOTE: Fee should be set to 0 for all inner transactions to ensure it is paid by an outer transaction sender instead of the Pool.
# No need for further fee checks.

const int POOL_TOKEN_TOTAL_SUPPLY = 18446744073709551615
const int LOCKED_POOL_TOKENS = 1000
const int ASSET_MIN_TOTAL = 10000
const bytes BYTE_ZERO = "\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"
const bytes POOL_TEMPLATE = "\x06\x80\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C"

if Txn.ApplicationID == 0:
    # create app
    app_global_put("fee_setter", Txn.Sender)
    app_global_put("fee_collector", Txn.Sender)
    app_global_put("fee_manager", Txn.Sender)
    exit(1)
end

switch Txn.OnCompletion:
    NoOp: main
    OptIn: bootstrap
    CloseOut: handle_closeout
    UpdateApplication: handle_updateapp
    DeleteApplication: handle_deleteapp
end

block handle_updateapp:
    exit(0)
end

block handle_deleteapp:
    exit(0)
end

block handle_closeout:
    exit(0)
end

block bootstrap:
    # Prerequisite: Pay Algo to Pool Address from User to cover minimum balance
    
    # Txn: AppCall with Optin from Pool Address to Bootstrap pool & ReKey to Application
    #   itxn[0]: Pay Algo from Pool to Application
    #   itxn[1]: Create Pool Token Asset from Application
    #   itxn[2]: Optin Pool to Asset 1
    #   itxn[3]: Optin Pool to Asset 2
    #   itxn[4]: Optin Pool to Pool Token Asset
    #   itxn[5]: Transfer Pool Token total supply to Pool Account

    # Should fail if:
    # Pool Address (Sender) != SHA512_256("program" + bytes from template and args)
    # Address already opted in to app
    # Required Algo is not sent to the pool address to cover minimum balance

    assert(Txn.ApplicationArgs[0] == "bootstrap")

    # Ensure Txn includes rekey to application address
    assert(Txn.RekeyTo == Global.CurrentApplicationAddress)

    int asset_1_id = Txn.Assets[0]
    int asset_2_id = Txn.Assets[1]
    assert(asset_1_id > asset_2_id)

    bytes pool_address = Txn.Sender
    int exists
    bytes asset_1_unit_name
    bytes asset_2_unit_name
    int asset_total

    exists, asset_1_unit_name = asset_params_get(AssetUnitName, asset_1_id)
    assert(exists)
    # Ensure Asset 1 has total supply >= ASSET_MIN_TOTAL
    _, asset_total = asset_params_get(AssetTotal, asset_1_id)
    assert(asset_total >= ASSET_MIN_TOTAL)

    if asset_2_id == 0:
        asset_2_unit_name = "ALGO"
    else:
        exists, asset_2_unit_name = asset_params_get(AssetUnitName, asset_2_id)
        assert(exists)
        # Ensure Asset 2 has total supply >= ASSET_MIN_TOTAL
        _, asset_total = asset_params_get(AssetTotal, asset_2_id)
        assert(asset_total >= ASSET_MIN_TOTAL)
    end

    bytes pool_token_asset_name = concat("TinymanPool2.0 ", concat(concat(asset_1_unit_name, "-"), asset_2_unit_name))

    # Ensure pool_address == SHA512_256("program" + bytes from template and args)
    bytes program = replace2(3, POOL_TEMPLATE, itob(Global.CurrentApplicationID))
    program = replace2(11, program, itob(asset_1_id))
    program = replace2(19, program, itob(asset_2_id))
    assert(pool_address == sha512_256(concat("Program", program)))

    # Use the metadata hash field of the Pool Token asset to store the asset_1_id & asset_2_id.
    # uint64 (8 bytes) + uint64 (8 bytes) + zeros (16 bytes)
    bytes metadata_hash = concat(itob(asset_1_id), concat(itob(asset_2_id), bzero(16)))

    # itxn[0]: Pay Algo from Pool to Application to fund minimum balance increase because of asset Creation
    inner_txn:
        TypeEnum: Pay
        Sender: pool_address
        Receiver: Global.CurrentApplicationAddress
        Amount: 100000
        Fee: 0
    end

    # itxn[1]: Create Pool Token Asset from Application Address
    inner_txn:
        TypeEnum: Acfg
        Sender: Global.CurrentApplicationAddress
        ConfigAssetUnitName: "TMPOOL2"
        ConfigAssetName: pool_token_asset_name
        ConfigAssetTotal: POOL_TOKEN_TOTAL_SUPPLY
        ConfigAssetDecimals: 6
        ConfigAssetURL: "https://tinyman.org"
        ConfigAssetReserve: pool_address
        ConfigAssetMetadataHash: metadata_hash
        Fee: 0
    end

    # Get the id of the asset just created
    int pool_token_asset_id = Itxn.CreatedAssetID

    # itxn[2]: Optin Pool to Asset 1
    inner_txn:
        TypeEnum: Axfer
        Sender: pool_address
        AssetReceiver: pool_address
        XferAsset: asset_1_id
        Amount: 0
        Fee: 0
    end

    # itxn[3]: Optin Pool to Asset 2
    if asset_2_id > 0:
        inner_txn:
            TypeEnum: Axfer
            Sender: pool_address
            AssetReceiver: pool_address
            XferAsset: asset_2_id
            Amount: 0
            Fee: 0
        end
    end

    # itxn[4]: Optin Pool to Pool Token Asset
    inner_txn:
        TypeEnum: Axfer
        Sender: pool_address
        AssetReceiver: pool_address
        XferAsset: pool_token_asset_id
        Amount: 0
        Fee: 0
    end

    # itxn[5]: Transfer Pool Token total supply to Pool Account
    inner_txn:
        TypeEnum: Axfer
        Sender: Global.CurrentApplicationAddress
        AssetReceiver: pool_address
        XferAsset: pool_token_asset_id
        AssetAmount: POOL_TOKEN_TOTAL_SUPPLY
        Fee: 0
    end

    # State updates
    # [asset_1]
    # [asset_2]
    # [pool_token_asset_id]
    app_local_put(0, "asset_1_id", asset_1_id)
    app_local_put(0, "asset_2_id", asset_2_id)
    app_local_put(0, "pool_token_asset_id", pool_token_asset_id)

    # Set all remaining keys with initials values
    app_local_put(0, "total_fee_share", 30)
    app_local_put(0, "protocol_fee_ratio", 6)
    app_local_put(0, "asset_1_reserves", 0)
    app_local_put(0, "asset_2_reserves", 0)
    app_local_put(0, "issued_pool_tokens", 0)
    app_local_put(0, "asset_1_protocol_fees", 0)
    app_local_put(0, "asset_2_protocol_fees", 0)
    app_local_put(0, "lock", 0)
    app_local_put(0, "asset_1_cumulative_price", BYTE_ZERO)
    app_local_put(0, "asset_2_cumulative_price", BYTE_ZERO)
    app_local_put(0, "cumulative_price_update_timestamp", Global.LatestTimestamp)
    exit(1)
end

block main:
    bytes user_address = Txn.Sender
    switch Txn.ApplicationArgs[0]:
        "set_fee_collector": set_fee_collector
        "set_fee_setter": set_fee_setter
        "set_fee_manager": set_fee_manager
        "claim_fees": claim_fees
        "claim_extra": claim_extra
        "set_fee": set_fee
        else: amm
    end

    block set_fee_collector:
        # Set a new fee collector, only fee manager can call this method
        # Txn: AppCall from fee_manager

        assert(user_address == app_global_get("fee_manager"))

        # State updates
        # [fee_collector]
        app_global_put("fee_collector", Txn.Accounts[1])
        exit(1)
    end

    block set_fee_setter:
        # Set a new fee setter, only fee manager can call this method
        # Txn: AppCall from fee_manager

        assert(user_address == app_global_get("fee_manager"))

        # State updates
        # [fee_setter]
        app_global_put("fee_setter", Txn.Accounts[1])
        exit(1)
    end

    block set_fee_manager:
        # Set a new fee manager, only fee manager can call this method
        # Txn: AppCall from fee_manager

        assert(user_address == app_global_get("fee_manager"))

        # State updates
        # [fee_manager]
        app_global_put("fee_manager", Txn.Accounts[1])
        exit(1)
    end

    block claim_fees:
        # Transfer accumulated fees from the pool to the fee_collector
        # Txn: AppCall from User
        #   itxn[0]: Transfer Asset 1 to fee_collector from Pool
        #   itxn[1]: Transfer Asset 2 to fee_collector from Pool

        bytes pool_address = Txn.Accounts[1]
        int asset_1_id = app_local_get(1, "asset_1_id")
        int asset_2_id = app_local_get(1, "asset_2_id")
        int asset_1_protocol_fees = app_local_get(1, "asset_1_protocol_fees")
        int asset_2_protocol_fees = app_local_get(1, "asset_2_protocol_fees")

        assert(asset_1_protocol_fees || asset_2_protocol_fees)
        transfer(asset_1_id, asset_1_protocol_fees, pool_address, app_global_get("fee_collector"))
        transfer(asset_2_id, asset_2_protocol_fees, pool_address, app_global_get("fee_collector"))

        # State updates
        app_local_put(1, "asset_1_protocol_fees", 0)
        app_local_put(1, "asset_2_protocol_fees", 0)
        exit(1)
    end

    block claim_extra:
        # Transfer any extra (donations) to the fee_collector

        # Txn: AppCall from User
        # NOTE: must account for min balance when asset_2 is algo
        #   itxn[0]: Transfer Asset 1 to fee_collector from Pool
        #   itxn[1]: Transfer Asset 2 to fee_collector from Pool

        bytes pool_address = Txn.Accounts[1]
        int asset_1_id = app_local_get(1, "asset_1_id")
        int asset_2_id = app_local_get(1, "asset_2_id")

        int asset_1_amount = get_balance(1, asset_1_id) - (app_local_get(1, "asset_1_reserves") + app_local_get(1, "asset_1_protocol_fees"))
        int asset_2_amount = get_balance(1, asset_2_id) - (app_local_get(1, "asset_2_reserves") + app_local_get(1, "asset_2_protocol_fees"))

        assert(asset_1_amount || asset_2_amount)
        transfer(asset_1_id, asset_1_amount, pool_address, app_global_get("fee_collector"))
        transfer(asset_2_id, asset_2_amount, pool_address, app_global_get("fee_collector"))
        exit(1)
    end

    block set_fee:
        # Set a new fee collector, only fee setter can call this method
        # Txn: AppCall from fee_setter
        assert(user_address == app_global_get("fee_setter"))

        int total_fee_share = btoi(Txn.ApplicationArgs[1])
        int protocol_fee_ratio = btoi(Txn.ApplicationArgs[2])

        assert(total_fee_share <= 100)
        assert(total_fee_share >= 1)
        assert(protocol_fee_ratio <= 10)
        assert(protocol_fee_ratio >= 3)

        # State updates
        # [total_fee_share]
        # [protocol_fee_ratio]
        app_local_put(1, "total_fee_share", total_fee_share)
        app_local_put(1, "protocol_fee_ratio", protocol_fee_ratio)
        exit(1)
    end

    block amm:
        bytes pool_address = Txn.Accounts[1]
        # These will fail if the account is not optted-in and only pool accounts can opt-in to the app.
        int asset_1_id = app_local_get(1, "asset_1_id")
        int asset_2_id = app_local_get(1, "asset_2_id")
        int pool_token_asset_id = app_local_get(1, "pool_token_asset_id")
        int asset_1_reserves = app_local_get(1, "asset_1_reserves")
        int asset_2_reserves = app_local_get(1, "asset_2_reserves")
        int issued_pool_tokens = app_local_get(1, "issued_pool_tokens")
        int asset_1_protocol_fees = app_local_get(1, "asset_1_protocol_fees")
        int asset_2_protocol_fees = app_local_get(1, "asset_2_protocol_fees")

        assert((Txn.ApplicationArgs[0] == "verify_flash_swap") || (app_local_get(1, "lock") == 0))

        switch Txn.ApplicationArgs[0]:
            "add_liquidity": add_liquidity
            "remove_liquidity": remove_liquidity
            "swap": swap
            "flash_loan": flash_loan
            "verify_flash_loan": verify_flash_loan
            "flash_swap": flash_swap
            "verify_flash_swap": verify_flash_swap
        end

        block swap:
            update_price_oracle()
            # Gtxn[N-1]: Transfer Asset1 to Pool from User
            # Gtxn[N]: AppCall from User
            #   itxn[0]: Transfer Asset 2 to User from Pool

            bytes k_initial = itob(asset_1_reserves) b* itob(asset_2_reserves)
            int input_txn_index = Txn.GroupIndex - 1
            int min_output = btoi(Txn.ApplicationArgs[1])
            bytes mode = Txn.ApplicationArgs[2]
            int input_asset_id
            int output_asset_id
            int input_amount

            if Gtxn[input_txn_index].TypeEnum == Pay:
                assert(Gtxn[input_txn_index].Receiver == pool_address)
                input_asset_id = 0
                input_amount = Gtxn[input_txn_index].Amount
            elif Gtxn[input_txn_index].TypeEnum == Axfer:
                assert(Gtxn[input_txn_index].AssetReceiver == pool_address)
                input_asset_id = Gtxn[input_txn_index].XferAsset
                input_amount = Gtxn[input_txn_index].AssetAmount
            else:
                error()
            end
            assert(Gtxn[input_txn_index].Sender == user_address)

            int input_supply
            int output_supply
            if input_asset_id == asset_1_id:
                output_asset_id = asset_2_id
                input_supply = asset_1_reserves
                output_supply = asset_2_reserves
            elif input_asset_id == asset_2_id:
                output_asset_id = asset_1_id
                input_supply = asset_2_reserves
                output_supply = asset_1_reserves
            else:
                error()
            end

            int total_fee_amount
            int poolers_fee_amount
            int protocol_fee_amount
            int swap_amount
            int output_amount
            int change = 0
            if mode == 'fixed-input':
                total_fee_amount, poolers_fee_amount, protocol_fee_amount = calculate_fixed_input_fee_amounts(input_amount)
                swap_amount = input_amount - total_fee_amount
                output_amount = calculate_fixed_input_swap(input_supply, output_supply, swap_amount)

                assert(total_fee_amount)
                assert(output_amount >= min_output)
            elif mode == 'fixed-output':
                output_amount = min_output
                swap_amount = calculate_fixed_output_swap(input_supply, output_supply, output_amount)
                total_fee_amount, poolers_fee_amount, protocol_fee_amount = calculate_fixed_output_fee_amounts(swap_amount)
                int required_input_amount = swap_amount + total_fee_amount

                assert(total_fee_amount)
                assert(input_amount >= required_input_amount)

                change = input_amount - required_input_amount
                if change:
                    transfer_to_user(input_asset_id, change)
                end
            else:
                error()
            end

            if input_asset_id == asset_1_id:
                asset_1_protocol_fees = asset_1_protocol_fees + protocol_fee_amount
                asset_1_reserves = asset_1_reserves + (swap_amount + poolers_fee_amount)
                asset_2_reserves = asset_2_reserves - output_amount
            else:
                asset_2_protocol_fees = asset_2_protocol_fees + protocol_fee_amount
                asset_2_reserves = asset_2_reserves + (swap_amount + poolers_fee_amount)
                asset_1_reserves = asset_1_reserves - output_amount
            end
            assert(k_initial b< (itob(asset_1_reserves) b* itob(asset_2_reserves)))

            transfer_to_user(output_asset_id, output_amount)

            # Logs
            log(concat("input_asset_id %i", itob(input_asset_id)))
            log(concat("input_amount %i", itob(input_amount)))
            log(concat("swap_amount %i", itob(swap_amount)))
            log(concat("change %i", itob(change)))

            log(concat("output_asset_id %i", itob(output_asset_id)))
            log(concat("output_amount %i", itob(output_amount)))

            log(concat("poolers_fee_amount %i", itob(poolers_fee_amount)))
            log(concat("protocol_fee_amount %i", itob(protocol_fee_amount)))
            log(concat("total_fee_amount %i", itob(total_fee_amount)))

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves)
            app_local_put(1, "asset_2_reserves", asset_2_reserves)
            app_local_put(1, "asset_1_protocol_fees", asset_1_protocol_fees)
            app_local_put(1, "asset_2_protocol_fees", asset_2_protocol_fees)
            exit(1)
        end

        block flash_loan:
            update_price_oracle()
            # Gtxn[N]: Flash Loan AppCall from User
            #   itxn: Transfer Asset 1 to User from Pool if Asset 1 is requested
            #   itxn: Transfer Asset 2 to User from Pool if Asset 2 is requested

            # Gtxn[N+X]: Verify Flash Loan AppCall from User

            int index_diff = btoi(Txn.ApplicationArgs[1])
            int verify_flash_loan_txn_index = Txn.GroupIndex + index_diff
            assert(Gtxn[verify_flash_loan_txn_index].TypeEnum == Appl)
            assert(Gtxn[verify_flash_loan_txn_index].ApplicationID == Global.CurrentApplicationID)
            assert(Gtxn[verify_flash_loan_txn_index].ApplicationArgs[0] == "verify_flash_loan")
            assert(Gtxn[verify_flash_loan_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])
            assert(Gtxn[verify_flash_loan_txn_index].Sender == user_address)
            int asset_1_amount = btoi(Txn.ApplicationArgs[2])
            int asset_2_amount = btoi(Txn.ApplicationArgs[3])
            assert(asset_1_amount || asset_2_amount)

            if asset_1_amount:
                assert(asset_1_amount <= asset_1_reserves)
                transfer_to_user(asset_1_id, asset_1_amount)
            end
            if asset_2_amount:
                assert(asset_2_amount <= asset_2_reserves)
                transfer_to_user(asset_2_id, asset_2_amount)
            end
            exit(1)
        end

        block verify_flash_loan:
            # Gtxn[N-X]: Flash Loan AppCall from User

            # if borrowed in two assets:
            #   Gtxn[N-2]: Transfer Asset 1 to Pool
            #   Gtxn[N-1]: Transfer Asset 2 to Pool
            # if borrowed single asset:
            #   Gtxn[N-1]: Transfer borrowed Asset to Pool
            # Gtxn[N]: Verify Flash Loan AppCall from User

            int index_diff = btoi(Txn.ApplicationArgs[1])
            int flash_loan_txn_index = Txn.GroupIndex - index_diff
            assert(Gtxn[flash_loan_txn_index].TypeEnum == Appl)
            assert(Gtxn[flash_loan_txn_index].ApplicationID == Global.CurrentApplicationID)
            assert(Gtxn[flash_loan_txn_index].ApplicationArgs[0] == "flash_loan")
            assert(Gtxn[flash_loan_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])
            assert(Gtxn[flash_loan_txn_index].Sender == user_address)
            int asset_1_output_amount = btoi(Gtxn[flash_loan_txn_index].ApplicationArgs[2])
            int asset_2_output_amount = btoi(Gtxn[flash_loan_txn_index].ApplicationArgs[3])

            if asset_1_output_amount:
                int asset_1_total_fee_amount
                int asset_1_poolers_fee_amount
                int asset_1_protocol_fee_amount
                int asset_1_repayment_amount
                
                asset_1_total_fee_amount, asset_1_poolers_fee_amount, asset_1_protocol_fee_amount = calculate_fixed_input_fee_amounts(asset_1_output_amount)
                asset_1_repayment_amount = asset_1_output_amount + asset_1_total_fee_amount
                assert(asset_1_repayment_amount > asset_1_output_amount)
                
                int asset_1_txn_index
                if asset_2_output_amount:
                    asset_1_txn_index = Txn.GroupIndex - 2
                else:
                    asset_1_txn_index = Txn.GroupIndex - 1
                end

                assert(Gtxn[asset_1_txn_index].TypeEnum == Axfer)
                assert(Gtxn[asset_1_txn_index].XferAsset == asset_1_id)
                assert(Gtxn[asset_1_txn_index].Sender == user_address)
                assert(Gtxn[asset_1_txn_index].AssetReceiver == pool_address)
                assert(Gtxn[asset_1_txn_index].AssetAmount >= asset_1_repayment_amount)
                int asset_1_input_amount = Gtxn[asset_1_txn_index].AssetAmount
                int asset_1_donation_amount = asset_1_input_amount - asset_1_repayment_amount

                asset_1_protocol_fees = asset_1_protocol_fees + asset_1_protocol_fee_amount
                asset_1_reserves = asset_1_reserves + asset_1_poolers_fee_amount

                # Logs
                log(concat("asset_1_output_amount %i", itob(asset_1_output_amount)))
                log(concat("asset_1_input_amount %i", itob(asset_1_input_amount)))
                log(concat("asset_1_donation_amount %i", itob(asset_1_donation_amount)))
                log(concat("asset_1_poolers_fee_amount %i", itob(asset_1_poolers_fee_amount)))
                log(concat("asset_1_protocol_fee_amount %i", itob(asset_1_protocol_fee_amount)))
                log(concat("asset_1_total_fee_amount %i", itob(asset_1_total_fee_amount)))
            end

            if asset_2_output_amount:
                int asset_2_total_fee_amount
                int asset_2_poolers_fee_amount
                int asset_2_protocol_fee_amount
                int asset_2_repayment_amount
                
                asset_2_total_fee_amount, asset_2_poolers_fee_amount, asset_2_protocol_fee_amount = calculate_fixed_input_fee_amounts(asset_2_output_amount)
                asset_2_repayment_amount = asset_2_output_amount + asset_2_total_fee_amount
                assert(asset_2_repayment_amount > asset_2_output_amount)

                int asset_2_txn_index = Txn.GroupIndex - 1
                int asset_2_input_amount
                int asset_2_donation_amount
                if asset_2_id == 0:
                    assert(Gtxn[asset_2_txn_index].TypeEnum == Pay)
                    assert(Gtxn[asset_2_txn_index].Sender == user_address)
                    assert(Gtxn[asset_2_txn_index].Receiver == pool_address)
                    assert(Gtxn[asset_2_txn_index].Amount >= asset_2_repayment_amount)
                    asset_2_input_amount = Gtxn[asset_2_txn_index].Amount
                else:
                    assert(Gtxn[asset_2_txn_index].TypeEnum == Axfer)
                    assert(Gtxn[asset_2_txn_index].XferAsset == asset_2_id)
                    assert(Gtxn[asset_2_txn_index].Sender == user_address)
                    assert(Gtxn[asset_2_txn_index].AssetReceiver == pool_address)
                    assert(Gtxn[asset_2_txn_index].AssetAmount >= asset_2_repayment_amount)
                    asset_2_input_amount = Gtxn[asset_2_txn_index].AssetAmount
                end

                asset_2_donation_amount = asset_2_input_amount - asset_2_repayment_amount
                asset_2_protocol_fees = asset_2_protocol_fees + asset_2_protocol_fee_amount
                asset_2_reserves = asset_2_reserves + asset_2_poolers_fee_amount

                # Logs
                log(concat("asset_2_output_amount %i", itob(asset_2_output_amount)))
                log(concat("asset_2_input_amount %i", itob(asset_2_input_amount)))
                log(concat("asset_2_donation_amount %i", itob(asset_2_donation_amount)))
                log(concat("asset_2_poolers_fee_amount %i", itob(asset_2_poolers_fee_amount)))
                log(concat("asset_2_protocol_fee_amount %i", itob(asset_2_protocol_fee_amount)))
                log(concat("asset_2_total_fee_amount %i", itob(asset_2_total_fee_amount)))
            end

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves)
            app_local_put(1, "asset_2_reserves", asset_2_reserves)
            app_local_put(1, "asset_1_protocol_fees", asset_1_protocol_fees)
            app_local_put(1, "asset_2_protocol_fees", asset_2_protocol_fees)
            exit(1)
        end

        block flash_swap:
            update_price_oracle()
            # Gtxn[N]: Flash Swap AppCall from User
            #   itxn: Transfer Asset 1 to User from Pool if Asset 1 is requested
            #   itxn: Transfer Asset 2 to User from Pool if Asset 2 is requested

            # Gtxn[N+X]: Verify Flash Swap AppCall from User

            int index_diff = btoi(Txn.ApplicationArgs[1])
            int verify_flash_swap_txn_index = Txn.GroupIndex + index_diff
            assert(Gtxn[verify_flash_swap_txn_index].TypeEnum == Appl)
            assert(Gtxn[verify_flash_swap_txn_index].ApplicationID == Global.CurrentApplicationID)
            assert(Gtxn[verify_flash_swap_txn_index].ApplicationArgs[0] == "verify_flash_swap")
            assert(Gtxn[verify_flash_swap_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])
            assert(Gtxn[verify_flash_swap_txn_index].Sender == user_address)
            int asset_1_output_amount = btoi(Txn.ApplicationArgs[2])
            int asset_2_output_amount = btoi(Txn.ApplicationArgs[3])
            assert(asset_1_output_amount || asset_2_output_amount)

            if asset_1_output_amount:
                transfer_to_user(asset_1_id, asset_1_output_amount)
            end
            if asset_2_output_amount:
                transfer_to_user(asset_2_id, asset_2_output_amount)
            end

            # Share data between app calls
            # initial_k
            log(itob(asset_1_reserves) b* itob(asset_2_reserves))
            # asset_1_balance_after_transfer
            log(itob(get_balance(1, asset_1_id)))
            # asset_2_balance_after_transfer
            log(itob(get_balance(1, asset_2_id)))

            app_local_put(1, "lock", 1)
            exit(1)
        end

        block verify_flash_swap:
            # Gtxn[N-X]: Flash Swap AppCall from User
            # Gtxn[N]: Verify Flash Swap AppCall from User

            int index_diff = btoi(Txn.ApplicationArgs[1])
            int flash_swap_txn_index = Txn.GroupIndex - index_diff
            assert(Gtxn[flash_swap_txn_index].TypeEnum == Appl)
            assert(Gtxn[flash_swap_txn_index].ApplicationID == Global.CurrentApplicationID)
            assert(Gtxn[flash_swap_txn_index].ApplicationArgs[0] == "flash_swap")
            assert(Gtxn[flash_swap_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])
            int asset_1_output_amount = btoi(Gtxn[flash_swap_txn_index].ApplicationArgs[2])
            int asset_2_output_amount = btoi(Gtxn[flash_swap_txn_index].ApplicationArgs[3])

            bytes initial_k = Gtxn[flash_swap_txn_index].Logs[0]
            int asset_1_balance_after_transfer = btoi(Gtxn[flash_swap_txn_index].Logs[1])
            int asset_2_balance_after_transfer = btoi(Gtxn[flash_swap_txn_index].Logs[2])
            int asset_1_balance = get_balance(1, asset_1_id)
            int asset_2_balance = get_balance(1, asset_2_id)

            int asset_1_input_amount = 0
            if asset_1_balance > asset_1_balance_after_transfer:
                asset_1_input_amount = asset_1_balance - asset_1_balance_after_transfer
            end

            int asset_2_input_amount = 0
            if asset_2_balance > asset_2_balance_after_transfer:
                asset_2_input_amount = asset_2_balance - asset_2_balance_after_transfer
            end

            int asset_1_total_fee_amount = 0
            int asset_1_poolers_fee_amount = 0
            int asset_1_protocol_fee_amount = 0
            if asset_1_input_amount:
                asset_1_total_fee_amount, asset_1_poolers_fee_amount, asset_1_protocol_fee_amount = calculate_fixed_input_fee_amounts(asset_1_input_amount)
                asset_1_protocol_fees = asset_1_protocol_fees + asset_1_protocol_fee_amount
                asset_1_reserves = (asset_1_reserves - asset_1_output_amount) + (asset_1_input_amount - asset_1_protocol_fee_amount)
            else:
                asset_1_reserves = asset_1_reserves - asset_1_output_amount
            end

            int asset_2_total_fee_amount = 0
            int asset_2_poolers_fee_amount = 0
            int asset_2_protocol_fee_amount = 0
            if asset_2_input_amount:
                asset_2_total_fee_amount, asset_2_poolers_fee_amount, asset_2_protocol_fee_amount = calculate_fixed_input_fee_amounts(asset_2_input_amount)
                asset_2_protocol_fees = asset_2_protocol_fees + asset_2_protocol_fee_amount
                asset_2_reserves = (asset_2_reserves - asset_2_output_amount) + (asset_2_input_amount - asset_2_protocol_fee_amount)
            else:
                asset_2_reserves = asset_2_reserves - asset_2_output_amount
            end

            assert(initial_k b< (itob(asset_1_reserves - asset_1_poolers_fee_amount) b* itob(asset_2_reserves - asset_2_poolers_fee_amount)))

            # Logs
            log(concat("asset_1_output_amount %i", itob(asset_1_output_amount)))
            log(concat("asset_1_input_amount %i", itob(asset_1_input_amount)))
            log(concat("asset_1_poolers_fee_amount %i", itob(asset_1_poolers_fee_amount)))
            log(concat("asset_1_protocol_fee_amount %i", itob(asset_1_protocol_fee_amount)))
            log(concat("asset_1_total_fee_amount %i", itob(asset_1_total_fee_amount)))

            log(concat("asset_2_output_amount %i", itob(asset_2_output_amount)))
            log(concat("asset_2_input_amount %i", itob(asset_2_input_amount)))
            log(concat("asset_2_poolers_fee_amount %i", itob(asset_2_poolers_fee_amount)))
            log(concat("asset_2_protocol_fee_amount %i", itob(asset_2_protocol_fee_amount)))
            log(concat("asset_2_total_fee_amount %i", itob(asset_2_total_fee_amount)))

            # State updates
            app_local_put(1, "lock", 0)
            app_local_put(1, "asset_1_reserves", asset_1_reserves)
            app_local_put(1, "asset_2_reserves", asset_2_reserves)
            app_local_put(1, "asset_1_protocol_fees", asset_1_protocol_fees)
            app_local_put(1, "asset_2_protocol_fees", asset_2_protocol_fees)
            exit(1)
        end

        block add_liquidity:
            # Gtxn[N-2]: Transfer Asset1 to Pool from User
            # Gtxn[N-1]: Transfer Asset2 to Pool from User
            # Gtxn[N]: AppCall from User
            #   itxn[0]: Transfer Pool Token to User from Pool
            #   itxn[1]: Transfer Asset 1 or 2 to User from Pool (change)

            int asset_1_txn_index = Txn.GroupIndex - 2
            int asset_2_txn_index = Txn.GroupIndex - 1

            assert(Gtxn[asset_1_txn_index].TypeEnum == Axfer)
            assert(Gtxn[asset_1_txn_index].AssetReceiver == pool_address)
            assert(Gtxn[asset_1_txn_index].XferAsset == asset_1_id)
            assert(Gtxn[asset_1_txn_index].Sender == user_address)
            int asset_1_amount = Gtxn[asset_1_txn_index].AssetAmount

            int asset_2_amount
            if asset_2_id == 0:
                assert(Gtxn[asset_2_txn_index].TypeEnum == Pay)
                assert(Gtxn[asset_2_txn_index].Receiver == pool_address)
                asset_2_amount = Gtxn[asset_2_txn_index].Amount
            else:
                assert(Gtxn[asset_2_txn_index].TypeEnum == Axfer)
                assert(Gtxn[asset_2_txn_index].AssetReceiver == pool_address)
                assert(Gtxn[asset_2_txn_index].XferAsset == asset_2_id)
                asset_2_amount = Gtxn[asset_2_txn_index].AssetAmount
            end
            assert(Gtxn[asset_2_txn_index].Sender == user_address)

            int pool_tokens_out
            if issued_pool_tokens:
                # Adding liquidity after it already has some liquidity
                bytes a_temp = itob(asset_1_amount) b* itob(issued_pool_tokens)
                bytes b_temp = itob(asset_2_amount) b* itob(issued_pool_tokens)
                int pool_tokens_output_a = btoi(a_temp b/ itob(asset_1_reserves))
                int pool_tokens_output_b = btoi(b_temp b/ itob(asset_2_reserves))

                if pool_tokens_output_a > pool_tokens_output_b:
                    # Less of asset 2 supplied. Pool tokens will be issued proportionally to asset_2_amount (b)
                    # Calculate amount of asset 1 to return to user as change
                    pool_tokens_out = pool_tokens_output_b
                    int expected_asset_1_amount = btoi((itob(pool_tokens_out) b* itob(asset_1_reserves)) b/ itob(issued_pool_tokens))
                    
                    # Round Up if there is remainder
                    if btoi(b_temp b% itob(asset_2_reserves)):
                        expected_asset_1_amount = expected_asset_1_amount + 1
                    end
                    
                    int asset_1_change = asset_1_amount - expected_asset_1_amount
                    asset_1_amount = expected_asset_1_amount
                    transfer_to_user(asset_1_id, asset_1_change)
                else:
                    # Less of asset 1 supplied. Pool tokens will be issued proportionally to asset_1_amount (a)
                    # Calculate amount of asset 2 to return to user as change
                    pool_tokens_out = pool_tokens_output_a
                    int expected_asset_2_amount = btoi((itob(pool_tokens_out) b* itob(asset_2_reserves)) b/ itob(issued_pool_tokens))

                    # Round Up if there is remainder                  
                    if btoi(a_temp b% itob(asset_1_reserves)):
                        expected_asset_2_amount = expected_asset_2_amount + 1
                    end

                    int asset_2_change = asset_2_amount - expected_asset_2_amount
                    asset_2_amount = expected_asset_2_amount
                    transfer_to_user(asset_2_id, asset_2_change)
                end
                issued_pool_tokens = issued_pool_tokens + pool_tokens_out
                asset_1_reserves = asset_1_reserves + asset_1_amount
                asset_2_reserves = asset_2_reserves + asset_2_amount
            else:
                # Adding liquidity to the pool for the first time:
                # pool_tokens_out = sqrt(asset_1_amount * asset_2_amount) - LOCKED_POOL_TOKENS
                issued_pool_tokens = btoi(bsqrt(itob(asset_1_amount) b* itob(asset_2_amount)))
                assert(issued_pool_tokens >= LOCKED_POOL_TOKENS)
                pool_tokens_out = issued_pool_tokens - LOCKED_POOL_TOKENS
                asset_1_reserves = asset_1_amount
                asset_2_reserves = asset_2_amount
            end

            # Ensure calculated amount of pool tokens is > 0
            assert(pool_tokens_out)
            # Send pool tokens to liquidity provider
            transfer_to_user(pool_token_asset_id, pool_tokens_out)

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves)
            app_local_put(1, "asset_2_reserves", asset_2_reserves)
            app_local_put(1, "issued_pool_tokens", issued_pool_tokens)
            exit(1)
        end

        block remove_liquidity:
            # Gtxn[N-1]: Transfer Pool Token to Pool from User
            # Gtxn[N]: AppCall from User
            #   itxn[0]: Transfer Asset 1 to User from Pool
            #   itxn[1]: Transfer Asset 2 to User from Pool

            int pool_token_txn_index = Txn.GroupIndex - 1
            assert(Gtxn[pool_token_txn_index].TypeEnum == Axfer)
            assert(Gtxn[pool_token_txn_index].AssetReceiver == pool_address)
            assert(Gtxn[pool_token_txn_index].XferAsset == pool_token_asset_id)
            assert(Gtxn[pool_token_txn_index].Sender == user_address)
            int removed_pool_token_amount = Gtxn[pool_token_txn_index].AssetAmount

            int asset_1_amount
            int asset_2_amount
            if (removed_pool_token_amount + LOCKED_POOL_TOKENS) == issued_pool_tokens:
                asset_1_amount = asset_1_reserves
                asset_2_amount = asset_2_reserves
                removed_pool_token_amount = issued_pool_tokens
            else:
                asset_1_amount = btoi((itob(removed_pool_token_amount) b* itob(asset_1_reserves)) b/ itob(issued_pool_tokens))
                asset_2_amount = btoi((itob(removed_pool_token_amount) b* itob(asset_2_reserves)) b/ itob(issued_pool_tokens))
            end

            assert(asset_1_amount && asset_2_amount)
            transfer_to_user(asset_1_id, asset_1_amount)
            transfer_to_user(asset_2_id, asset_2_amount)

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves - asset_1_amount)
            app_local_put(1, "asset_2_reserves", asset_2_reserves - asset_2_amount)
            app_local_put(1, "issued_pool_tokens", issued_pool_tokens - removed_pool_token_amount)
            exit(1)
        end

        func transfer_to_user(asset_id: int, amount: int):
            return transfer(asset_id, amount, pool_address, user_address)
        end

        func update_price_oracle():
            bytes asset_1_cumulative_price = app_local_get(1, "asset_1_cumulative_price")
            bytes asset_2_cumulative_price = app_local_get(1, "asset_2_cumulative_price")
            int time_delta = Global.LatestTimestamp - app_local_get(1, "cumulative_price_update_timestamp")

            if (issued_pool_tokens && time_delta):
                asset_1_cumulative_price = asset_1_cumulative_price b+ (((itob(asset_2_reserves) b* TWO_TO_THE_64) b* itob(time_delta)) b/ itob(asset_1_reserves))
                asset_2_cumulative_price = asset_2_cumulative_price b+ (((itob(asset_1_reserves) b* TWO_TO_THE_64) b* itob(time_delta)) b/ itob(asset_2_reserves))
                app_local_put(1, "asset_1_cumulative_price", asset_1_cumulative_price)
                app_local_put(1, "asset_2_cumulative_price", asset_2_cumulative_price)
                app_local_put(1, "cumulative_price_update_timestamp", Global.LatestTimestamp)
            end
            return
        end
    end
end

func calculate_fixed_input_fee_amounts(input_amount: int) int, int, int:
    int total_fee_share = app_local_get(1, "total_fee_share")
    int protocol_fee_ratio = app_local_get(1, "protocol_fee_ratio")
    
    int total_fee = (input_amount * total_fee_share) / 10000
    int protocol_fee = total_fee / protocol_fee_ratio
    int poolers_fee = total_fee - protocol_fee
    return total_fee, poolers_fee, protocol_fee
end

func calculate_fixed_output_fee_amounts(swap_amount: int) int, int, int:
    int total_fee_share = app_local_get(1, "total_fee_share")
    int protocol_fee_ratio = app_local_get(1, "protocol_fee_ratio")
    int input_amount = (swap_amount * 10000) / (10000 - total_fee_share)

    int total_fee = (input_amount * total_fee_share) / 10000
    int protocol_fee = total_fee / protocol_fee_ratio
    int poolers_fee = total_fee - protocol_fee
    return total_fee, poolers_fee, protocol_fee
end

func calculate_fixed_input_swap(input_supply: int, output_supply: int, swap_amount: int) int:
    # Calculates the output amount for a fixed-input swap ignoring fees
    # k = input_supply * output_supply
    # output_amount = output_supply - (k / (input_supply + swap_amount))
    bytes k = itob(input_supply) b* itob(output_supply)
    # +1 for Round Up
    int output_amount = output_supply - (btoi((k b/ itob(input_supply + swap_amount))) + 1)
    return output_amount
end

func calculate_fixed_output_swap(input_supply: int, output_supply: int, output_amount: int) int:
    # Calculates the input amount for a fixed-output swap ignoring fees
    # k = input_supply * output_supply
    # input_amount = (k / (output_supply - asset_output_amount)) - input_supply
    bytes k = itob(input_supply) b* itob(output_supply)
    # +1 for Round Up
    int input_amount = (btoi((k b/ itob(output_supply - output_amount))) + 1) - input_supply
    return input_amount
end

func get_balance(account_idx: int, asset_id: int) int:
    int balance = 0
    if asset_id == 0:
        balance = balance(account_idx) - min_balance(account_idx)
    else:
        _, balance = asset_holding_get(AssetBalance, account_idx, asset_id)
    end
    return balance
end

func transfer(asset_id: int, amount: int, sender: bytes, receiver: bytes):
    if asset_id == 0:
        inner_txn:
            TypeEnum: Pay
            Sender: sender
            Receiver: receiver
            Amount: amount
            Fee: 0
        end
    else:
        inner_txn:
            TypeEnum: Axfer
            Sender: sender
            AssetReceiver: receiver
            AssetAmount: amount
            XferAsset: asset_id
            Fee: 0
        end
    end
    return
end
