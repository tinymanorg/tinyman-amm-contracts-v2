#pragma version 7

const int POOL_TOKEN_TOTAL_SUPPLY = 18446744073709551615
const int LOCKED_POOL_TOKENS = 1000
const int ASSET_MIN_TOTAL = 10000
const byte BYTE_ZERO = "\x00\x00\x00\x00\x00\x00\x00\x00"
const byte TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"

const byte ABI_ADD_LIQUIDITY = "E\x1d\x91\xb3"
const byte ABI_REMOVE_LIQUIDITY = "\x10`^T"
const byte ABI_SWAP = "\xba'-\xc5"
const byte ABI_FLASH = "\x9bQA\x1c"
const byte ABI_VERIFY_FLASH = "\xfd\xe6\xc1\xb5"
const byte ABI_CLAIM_FEES = "xb\xe4\xae"
const byte ABI_CLAIM_EXTRA = "z`\x8a\xa5"
const byte ABI_SET_FEE = "\xfe\x11\xcc["


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

exit(0)

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
    # Gtxn[0]: Pay Algo to Pool Address from User
    # Gtxn[1]: AppCall with Optin from Pool Address to Bootstrap pool & ReKey to Application

    # itxn[0]: Pay Algo from Pool to Application
    # itxn[1]: Create Pool Token Asset from Application
    # itxn[2]: Optin Pool to Asset 1
    # itxn[3]: Optin Pool to Asset 2
    # itxn[4]: Optin Pool to Pool Token Asset
    # itxn[5]: Transfer Pool Token total supply to Pool Account

    # Should fail if:
    # Pool Address (Sender) != SHA512_256("program" + bytes from template and args)
    # Address already opted in to app

    assert((Txn.ApplicationArgs[0] == "bootstrap") || (Txn.ApplicationArgs[0] == "\x1dd\x8dm"))

    # Ensure the same asset ids are included in Txn.ApplicationArgs and Txn.Assets
    int asset_1_id = btoi(Txn.ApplicationArgs[1])
    int asset_2_id = btoi(Txn.ApplicationArgs[2])
    assert(asset_1_id > asset_2_id)
    assert(asset_1_id == Txn.Assets[0])
    assert(asset_2_id == Txn.Assets[1])

    int exists
    byte asset_1_unit_name = ""
    byte asset_2_unit_name = "ALGO"
    int asset_total

    exists, asset_1_unit_name = asset_params_get(AssetUnitName, asset_1_id)
    assert(exists)

    # Ensure Asset 1 has total supply >= ASSET_MIN_TOTAL
    exists, asset_total = asset_params_get(AssetTotal, asset_1_id)
    assert(exists && (asset_total >= ASSET_MIN_TOTAL))

    if asset_2_id:
        exists, asset_2_unit_name = asset_params_get(AssetUnitName, asset_2_id)
        assert(exists)
        # Ensure Asset 2 has total supply >= ASSET_MIN_TOTAL
        exists, asset_total = asset_params_get(AssetTotal, asset_2_id)
        assert(exists && (asset_total >= ASSET_MIN_TOTAL))
    end

    byte pool_token_asset_name = concat("TinymanPool2.0 ", concat(concat(asset_1_unit_name, "-"), asset_2_unit_name))

    byte pool_address = Txn.Sender

    # Ensure pool_address == SHA512_256("program" + bytes from template and args)
    const byte TEMPLATE = "\x06\x80\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C"
    byte program = replace2(3, TEMPLATE, itob(Global.CurrentApplicationID))
    program = replace2(11, program, Txn.ApplicationArgs[1])
    program = replace2(19, program, Txn.ApplicationArgs[2])
    byte hash = sha512_256(concat("Program", program))
    assert(hash == pool_address)

    # Ensure Txn includes rekey to application address
    assert(Txn.RekeyTo == Global.CurrentApplicationAddress)

    # NOTE: Fee should be set to 0 for all inner transactions to ensure it is paid by an outer transaction sender instead of the Pool.
    # No need for further fee checks.

    # itxn[0]: Pay Algo from Pool to Application to fund Asset Creation
    inner_txn:
        TypeEnum: Pay
        Sender: pool_address
        Receiver: Global.CurrentApplicationAddress
        Amount: 200000
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

    int poolers_fee_share = 25
    int protocol_fee_share = 5
    # State updates
    # [pool_token_asset_id]
    # [asset_1]
    # [asset_2]
    # [poolers_fee_share]
    # [protocol_fee_share]
    app_local_put(0, "pool_token_asset_id", pool_token_asset_id)
    app_local_put(0, "asset_1_id", asset_1_id)
    app_local_put(0, "asset_2_id", asset_2_id)
    app_local_put(0, "poolers_fee_share", poolers_fee_share)
    app_local_put(0, "protocol_fee_share", protocol_fee_share)
    # Set all remaining keys with initials values
    app_local_put(0, "asset_1_reserves", 0)
    app_local_put(0, "asset_2_reserves", 0)
    app_local_put(0, "issued_pool_tokens", 0)
    app_local_put(0, "protocol_fees_asset_1", 0)
    app_local_put(0, "protocol_fees_asset_2", 0)
    app_local_put(0, "cumulative_asset_1_price", BYTE_ZERO)
    app_local_put(0, "cumulative_asset_2_price", BYTE_ZERO)
    app_local_put(0, "cumulative_price_update_timestamp", Global.LatestTimestamp)
    exit(1)
end


block main:
    # Protect the user from malicious clients that might hide a rekey in a swap call
    assert(Txn.RekeyTo == Global.ZeroAddress)

    byte user_address = Txn.Sender
    switch Txn.ApplicationArgs[0]:
        "set_fee_collector": set_fee_collector
        "set_fee_setter": set_fee_setter
        "set_fee_manager": set_fee_manager
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

    block amm:
        byte pool_address = Txn.Accounts[1]
        # These will fail if the account is not optted-in and only pool accounts can opt-in to the app.
        int asset_1_id = app_local_get(1, "asset_1_id")
        int asset_2_id = app_local_get(1, "asset_2_id")
        int pool_token_asset_id = app_local_get(1, "pool_token_asset_id")
        int asset_1_reserves = app_local_get(1, "asset_1_reserves")
        int asset_2_reserves = app_local_get(1, "asset_2_reserves")
        int issued_pool_tokens = app_local_get(1, "issued_pool_tokens")
        int protocol_fees_asset_1 = app_local_get(1, "protocol_fees_asset_1")
        int protocol_fees_asset_2 = app_local_get(1, "protocol_fees_asset_2")

        switch Txn.ApplicationArgs[0]:
            "swap": swap
            ABI_SWAP: swap
            "add_liquidity": add_liquidity
            ABI_ADD_LIQUIDITY: add_liquidity
            "remove_liquidity": remove_liquidity
            ABI_REMOVE_LIQUIDITY: remove_liquidity
            "flash": flash
            ABI_FLASH: flash
            "verify_flash": verify_flash
            ABI_VERIFY_FLASH: verify_flash
            "claim_fees": claim_fees
            ABI_CLAIM_FEES: claim_fees
            "claim_extra": claim_extra
            ABI_CLAIM_EXTRA: claim_extra
            "set_fee": set_fee
            ABI_SET_FEE: set_fee
        end


        block swap:
            update_price_oracle()
            # Gtxn[0]: Transfer Asset1 to Pool from User
            # Gtxn[1]: AppCall from User

            # itxn[0]: Transfer Asset 2 to User from Pool

            int input_asset_id = btoi(Txn.ApplicationArgs[1])
            int output_asset_id = btoi(Txn.ApplicationArgs[2])
            int min_output = btoi(Txn.ApplicationArgs[3])
            int mode = Txn.ApplicationArgs[4]

            int input_amount
            int input_txn_index = Txn.GroupIndex - 1
            if input_asset_id == 0:
                assert(Gtxn[input_txn_index].TypeEnum == Pay)
                assert(Gtxn[input_txn_index].Receiver == pool_address)
                input_amount = Gtxn[input_txn_index].Amount
            else:
                assert(Gtxn[input_txn_index].TypeEnum == Axfer)
                assert(Gtxn[input_txn_index].AssetReceiver == pool_address)
                assert(Gtxn[input_txn_index].XferAsset == input_asset_id)
                input_amount = Gtxn[input_txn_index].AssetAmount
            end

            # input_asset_id must be asset_1_id or asset_2_id
            # output_asset_id must be asset_1_id or asset_2_id
            # input_asset_id must not equal output_asset_id
            int input_supply
            int output_supply
            if (input_asset_id == asset_1_id) && (output_asset_id == asset_2_id):
                input_supply = asset_1_reserves
                output_supply = asset_2_reserves
            elif (input_asset_id == asset_2_id) && (output_asset_id == asset_1_id):
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
            if (mode == 'fixed-input') || (mode == '\x00\x0bfixed-input'):
                total_fee_amount, poolers_fee_amount, protocol_fee_amount = calculate_fixed_input_fee_amounts(input_amount)
                swap_amount = input_amount - total_fee_amount
                output_amount = calculate_fixed_input_swap(input_supply, output_supply, swap_amount)
                assert(output_amount >= min_output)
            elif (mode == 'fixed-output') || (mode == '\x00\x0cfixed-output'):
                output_amount = min_output
                swap_amount = calculate_fixed_output_swap(input_supply, output_supply, output_amount)
                total_fee_amount, poolers_fee_amount, protocol_fee_amount = calculate_fixed_output_fee_amounts(swap_amount)
                int total_input_amount = swap_amount + total_fee_amount
                int change = input_amount - total_input_amount
                if change:
                    transfer_to_user(input_asset_id, change)
                end
            else:
                error()
            end

            log(concat("input_amount %i", itob(input_amount)))
            log(concat("poolers_fee_amount %i", itob(poolers_fee_amount)))
            log(concat("protocol_fee_amount %i", itob(protocol_fee_amount)))
            log(concat("swap_amount %i", itob(swap_amount)))
            log(concat("output_amount %i", itob(output_amount)))
            log(concat("change %i", itob(change)))

            if input_asset_id == asset_1_id:
                protocol_fees_asset_1 = protocol_fees_asset_1 + protocol_fee_amount
                asset_1_reserves = asset_1_reserves + (swap_amount + poolers_fee_amount)
                asset_2_reserves = asset_2_reserves - output_amount
            else:
                protocol_fees_asset_2 = protocol_fees_asset_2 + protocol_fee_amount
                asset_2_reserves = asset_2_reserves + (swap_amount + poolers_fee_amount)
                asset_1_reserves = asset_1_reserves - output_amount
            end

            transfer_to_user(output_asset_id, output_amount)

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves)
            app_local_put(1, "asset_2_reserves", asset_2_reserves)
            app_local_put(1, "protocol_fees_asset_1", protocol_fees_asset_1)
            app_local_put(1, "protocol_fees_asset_2", protocol_fees_asset_2)
            exit(1)
        end

        block flash:
            update_price_oracle()

            int index_diff = btoi(Txn.ApplicationArgs[1])
            int asset_1_amount = btoi(Txn.ApplicationArgs[2])
            int asset_2_amount = btoi(Txn.ApplicationArgs[3])
            assert(asset_1_amount || asset_2_amount)

            int verify_flash_txn_index = Txn.GroupIndex + index_diff
            assert(Gtxn[verify_flash_txn_index].Sender == Txn.Sender)
            assert(Gtxn[verify_flash_txn_index].TypeEnum == Appl)
            assert(Gtxn[verify_flash_txn_index].ApplicationID == Global.CurrentApplicationID)
            assert((Gtxn[verify_flash_txn_index].ApplicationArgs[0] == "verify_flash") || (Gtxn[verify_flash_txn_index].ApplicationArgs[0] == ABI_VERIFY_FLASH))
            assert(Gtxn[verify_flash_txn_index].ApplicationArgs[1] == Txn.ApplicationArgs[1])     # index_diff
            assert(Gtxn[verify_flash_txn_index].ApplicationArgs[2] == Txn.ApplicationArgs[2])     # asset_1_amount
            assert(Gtxn[verify_flash_txn_index].ApplicationArgs[3] == Txn.ApplicationArgs[3])     # asset_2_amount

            if asset_1_amount:
                transfer_to_user(asset_1_id, asset_1_amount)
            end
            if asset_2_amount:
                transfer_to_user(asset_2_id, asset_2_amount)
            end
            exit(1)
        end

        block verify_flash:
            int index_diff = btoi(Txn.ApplicationArgs[1])
            int asset_1_amount = btoi(Txn.ApplicationArgs[2])
            int asset_2_amount = btoi(Txn.ApplicationArgs[3])

            int flash_txn_index = Txn.GroupIndex - index_diff
            assert(Gtxn[flash_txn_index].TypeEnum == Appl)
            assert(Gtxn[flash_txn_index].ApplicationID == Global.CurrentApplicationID)
            assert((Gtxn[flash_txn_index].ApplicationArgs[0] == "flash") || (Gtxn[flash_txn_index].ApplicationArgs[0] == ABI_FLASH))

            int asset_1_repayment_amount = 0
            int asset_2_repayment_amount = 0

            if asset_1_amount:
                int asset_1_total_fee_amount
                int asset_1_poolers_fee_amount
                int asset_1_protocol_fee_amount
                asset_1_total_fee_amount, asset_1_poolers_fee_amount, asset_1_protocol_fee_amount = calculate_fixed_input_fee_amounts(asset_1_amount)
                asset_1_repayment_amount = asset_1_amount + asset_1_total_fee_amount
                assert(asset_1_repayment_amount > asset_1_amount)
                
                int asset_1_txn_index
                if asset_2_amount:
                    asset_1_txn_index = Txn.GroupIndex - 2
                else:
                    asset_1_txn_index = Txn.GroupIndex - 1
                end

                assert(Gtxn[asset_1_txn_index].TypeEnum == Axfer)
                assert(Gtxn[asset_1_txn_index].AssetReceiver == pool_address)
                assert(Gtxn[asset_1_txn_index].XferAsset == asset_1_id)
                assert(Gtxn[asset_1_txn_index].AssetAmount >= asset_1_repayment_amount)         # TODO: This line can be removed for optimization.
                
                int change = Gtxn[asset_1_txn_index].AssetAmount - asset_1_repayment_amount
                if change:
                    transfer_to_user(asset_1_id, change)
                end

                protocol_fees_asset_1 = protocol_fees_asset_1 + asset_1_protocol_fee_amount
                asset_1_reserves = asset_1_reserves + asset_1_poolers_fee_amount
            end

            if asset_2_amount:
                int asset_2_total_fee_amount
                int asset_2_poolers_fee_amount
                int asset_2_protocol_fee_amount
                asset_2_total_fee_amount, asset_2_poolers_fee_amount, asset_2_protocol_fee_amount = calculate_fixed_input_fee_amounts(asset_2_amount)
                asset_2_repayment_amount = asset_2_amount + asset_2_total_fee_amount
                assert(asset_2_repayment_amount > asset_2_amount)

                int asset_2_txn_index = Txn.GroupIndex - 1
                int change = 0
                if asset_2_id == 0:
                    assert(Gtxn[asset_2_txn_index].TypeEnum == Pay)
                    assert(Gtxn[asset_2_txn_index].Receiver == pool_address)
                    assert(Gtxn[asset_2_txn_index].Amount >= asset_2_repayment_amount)          # TODO: This line can be removed for optimization.
                    change = Gtxn[asset_2_txn_index].Amount - asset_2_repayment_amount
                else:
                    assert(Gtxn[asset_2_txn_index].TypeEnum == Axfer)
                    assert(Gtxn[asset_2_txn_index].AssetReceiver == pool_address)
                    assert(Gtxn[asset_2_txn_index].XferAsset == asset_2_id)
                    assert(Gtxn[asset_2_txn_index].AssetAmount >= asset_2_repayment_amount)     # TODO: This line can be removed for optimization.
                    change = Gtxn[asset_2_txn_index].AssetAmount - asset_2_repayment_amount
                end

                if change:
                    transfer_to_user(asset_2_id, change)
                end

                protocol_fees_asset_2 = protocol_fees_asset_2 + asset_2_protocol_fee_amount
                asset_2_reserves = asset_2_reserves + asset_2_poolers_fee_amount
            end

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves)
            app_local_put(1, "asset_2_reserves", asset_2_reserves)
            app_local_put(1, "protocol_fees_asset_1", protocol_fees_asset_1)
            app_local_put(1, "protocol_fees_asset_2", protocol_fees_asset_2)
            exit(1)
        end

        block add_liquidity:
            # Gtxn[0]: Transfer Asset1 to Pool from User
            # Gtxn[1]: Transfer Asset2 to Pool from User
            # Gtxn[2]: AppCall from User

            # itxn[0]: Transfer Pool Token to User from Pool
            # itxn[1]: Transfer Asset 1 or 2 to User from Pool (change)

            int asset_1_txn_index = Txn.GroupIndex - 2
            assert(Gtxn[asset_1_txn_index].TypeEnum == Axfer)
            assert(Gtxn[asset_1_txn_index].AssetReceiver == pool_address)
            assert(Gtxn[asset_1_txn_index].XferAsset == asset_1_id)
            int asset_1_amount = Gtxn[asset_1_txn_index].AssetAmount

            int asset_2_txn_index = Txn.GroupIndex - 1
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

            int pool_tokens_out
            if issued_pool_tokens:
                # Adding liquidity after it already has some liquidity
                byte a_temp = itob(asset_1_amount) b* itob(issued_pool_tokens)
                byte b_temp = itob(asset_2_amount) b* itob(issued_pool_tokens)
                int pool_tokens_out_a = btoi(a_temp b/ itob(asset_1_reserves))
                int pool_tokens_out_b = btoi(b_temp b/ itob(asset_2_reserves))

                if pool_tokens_out_a > pool_tokens_out_b:
                    # Less of asset 2 supplied. Pool tokens will be issued proportionally to asset_2_amount (b)
                    # Calculate amount of asset 1 to return to user as change
                    pool_tokens_out = pool_tokens_out_b
                    int expected_asset_1_amount = btoi((itob(pool_tokens_out) b* itob(asset_1_reserves)) b/ itob(issued_pool_tokens))
                    
                    # Round Up if there is remainder
                    if btoi(b_temp b% itob(asset_2_reserves)):
                        expected_asset_1_amount = expected_asset_1_amount + 1
                    end
                    
                    int change = asset_1_amount - expected_asset_1_amount
                    asset_1_amount = expected_asset_1_amount
                    transfer_to_user(asset_1_id, change)
                else:
                    # Less of asset 1 supplied. Pool tokens will be issued proportionally to asset_1_amount (a)
                    # Calculate amount of asset 2 to return to user as change
                    pool_tokens_out = pool_tokens_out_a
                    int expected_asset_2_amount = btoi((itob(pool_tokens_out) b* itob(asset_2_reserves)) b/ itob(issued_pool_tokens))

                    # Round Up if there is remainder                  
                    if btoi(a_temp b% itob(asset_1_reserves)):
                        expected_asset_2_amount = expected_asset_2_amount + 1
                    end

                    int change = asset_2_amount - expected_asset_2_amount
                    asset_2_amount = expected_asset_2_amount
                    transfer_to_user(asset_2_id, change)
                end
                issued_pool_tokens = issued_pool_tokens + pool_tokens_out
                asset_1_reserves = asset_1_reserves + asset_1_amount
                asset_2_reserves = asset_2_reserves + asset_2_amount
            else:
                # Adding liquidity to the pool for the first time:
                # pool_tokens_out = sqrt(asset_1_amount * asset_2_amount) - LOCKED_POOL_TOKENS
                issued_pool_tokens = btoi(bsqrt(itob(asset_1_amount) b* itob(asset_2_amount)))
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
            # Gtxn[0]: Transfer Pool Token to Pool from User
            # Gtxn[1]: AppCall from User

            # itxn[0]: Transfer Asset 1 to User from Pool
            # itxn[1]: Transfer Asset 2 to User from Pool

            int pool_token_txn_index = Txn.GroupIndex - 1
            assert(Gtxn[pool_token_txn_index].TypeEnum == Axfer)
            assert(Gtxn[pool_token_txn_index].AssetReceiver == pool_address)
            assert(Gtxn[pool_token_txn_index].XferAsset == pool_token_asset_id)
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
            
            # TODO: Is it required? It projects the user but if the liquidity or share is too low, it may cause issues.
            assert(asset_1_amount && asset_2_amount)
            transfer_to_user(asset_1_id, asset_1_amount)
            transfer_to_user(asset_2_id, asset_2_amount)

            # State updates
            app_local_put(1, "asset_1_reserves", asset_1_reserves - asset_1_amount)
            app_local_put(1, "asset_2_reserves", asset_2_reserves - asset_2_amount)
            app_local_put(1, "issued_pool_tokens", issued_pool_tokens - removed_pool_token_amount)
            exit(1)
        end

        block claim_fees:
            # Transfer accumulated fees from the pool to the fee_collector

            # Gtxn[0]: AppCall from User

            # itxn[0]: Transfer Asset 1 to fee_collector from Pool
            # itxn[1]: Transfer Asset 2 to fee_collector from Pool

            assert(user_address == app_global_get("fee_collector"))
            assert(protocol_fees_asset_1 || protocol_fees_asset_2)
            transfer_to_user(asset_1_id, protocol_fees_asset_1)
            transfer_to_user(asset_2_id, protocol_fees_asset_2)

            # State updates
            app_local_put(1, "protocol_fees_asset_1", 0)
            app_local_put(1, "protocol_fees_asset_2", 0)
            exit(1)
        end

        block claim_extra:
            # Transfer any extra donations to the fee_collector

            # Gtxn[0]: AppCall from User
            # Note: must account for min balance when asset_2 is algo

            # itxn[0]: Transfer Asset 1 to fee_collector from Pool
            # itxn[1]: Transfer Asset 2 to fee_collector from Pool

            assert(user_address == app_global_get("fee_collector"))
            int asset_1_amount = get_balance(1, asset_1_id) - (asset_1_reserves + protocol_fees_asset_1)
            int asset_2_amount = get_balance(1, asset_2_id) - (asset_2_reserves + protocol_fees_asset_2)

            assert(asset_1_amount || asset_2_amount)
            transfer_to_user(asset_1_id, asset_1_amount)
            transfer_to_user(asset_2_id, asset_2_amount)
            exit(1)
        end

        block set_fee:
            # Set a new fee collector, only fee setter can call this method
            # Txn: AppCall from fee_setter
            assert(user_address == app_global_get("fee_setter"))

            int poolers_fee_share = btoi(Txn.ApplicationArgs[1])
            int protocol_fee_share = btoi(Txn.ApplicationArgs[2])

            # TODO: Dummy validation for now
            assert(poolers_fee_share <= 50)
            assert(protocol_fee_share <= 10)
            assert(poolers_fee_share >= protocol_fee_share)
            assert(poolers_fee_share >= (protocol_fee_share * 5))

            # State updates
            # [poolers_fee_share]
            # [protocol_fee_share]
            app_local_put(1, "poolers_fee_share", poolers_fee_share)
            app_local_put(1, "protocol_fee_share", protocol_fee_share)
            exit(1)
        end

        func transfer_to_user(asset_id: int, amount: int):
            if asset_id == 0:
                inner_txn:
                    TypeEnum: Pay
                    Sender: pool_address
                    Receiver: user_address
                    Amount: amount
                    Fee: 0
                end
            else:
                inner_txn:
                    TypeEnum: Axfer
                    Sender: pool_address
                    AssetReceiver: user_address
                    AssetAmount: amount
                    XferAsset: asset_id
                    Fee: 0
                end
            end
            return
        end

        func update_price_oracle():
            byte cumulative_asset_1_price = app_local_get(1, "cumulative_asset_1_price")
            byte cumulative_asset_2_price = app_local_get(1, "cumulative_asset_2_price")
            int time_delta = Global.LatestTimestamp - app_local_get(1, "cumulative_price_update_timestamp")

            if (issued_pool_tokens && time_delta):
                cumulative_asset_1_price = cumulative_asset_1_price b+ (((itob(asset_2_reserves) b* TWO_TO_THE_64) b* itob(time_delta)) b/ itob(asset_1_reserves))
                cumulative_asset_2_price = cumulative_asset_2_price b+ (((itob(asset_1_reserves) b* TWO_TO_THE_64) b* itob(time_delta)) b/ itob(asset_2_reserves))
                app_local_put(1, "cumulative_asset_1_price", cumulative_asset_1_price)
                app_local_put(1, "cumulative_asset_2_price", cumulative_asset_2_price)
                app_local_put(1, "cumulative_price_update_timestamp", Global.LatestTimestamp)
            end
            return
        end
    end
end


func calculate_fixed_input_fee_amounts(input_amount: int) int:
    int poolers_fee_share = app_local_get(1, "poolers_fee_share")
    int protocol_fee_share = app_local_get(1, "protocol_fee_share")
    
    int total_fee_share = poolers_fee_share + protocol_fee_share
    int total_fee = (input_amount * total_fee_share) / 10000
    int protocol_fee = (total_fee * protocol_fee_share) / total_fee_share
    int poolers_fee = total_fee - protocol_fee
    return total_fee, poolers_fee, protocol_fee
end


func calculate_fixed_output_fee_amounts(swap_amount: int) int:
    int poolers_fee_share = app_local_get(1, "poolers_fee_share")
    int protocol_fee_share = app_local_get(1, "protocol_fee_share")
    int input_amount = (swap_amount * 10000) / (10000 - (poolers_fee_share + protocol_fee_share))

    int total_fee_share = poolers_fee_share + protocol_fee_share
    int total_fee = (input_amount * total_fee_share) / 10000
    int protocol_fee = (total_fee * protocol_fee_share) / total_fee_share
    int poolers_fee = total_fee - protocol_fee
    return total_fee, poolers_fee, protocol_fee
end


func calculate_fixed_input_swap(input_supply: int, output_supply: int, swap_amount: int) int:
    # Calculates the output amount for a fixed-input swap ignoring fees
    # k = input_supply * output_supply
    # output_amount = output_supply - (k / (input_supply + swap_amount))
    byte k = itob(input_supply) b* itob(output_supply)
    int output_amount = output_supply - btoi((k b/ itob(input_supply + swap_amount)))
    return output_amount
end


func calculate_fixed_output_swap(input_supply: int, output_supply: int, output_amount: int) int:
    # Calculates the input amount for a fixed-output swap ignoring fees
    # k = input_supply * output_supply
    # input_amount = (k / (output_supply - asset_out_amount)) - input_supply
    byte k = itob(input_supply) b* itob(output_supply)
    int input_amount = btoi((k b/ itob(output_supply - output_amount))) - input_supply
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
