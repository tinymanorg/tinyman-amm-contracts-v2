#pragma version 7

const int POOL_TOKEN_TOTAL_SUPPLY = 18446744073709551615
const int LOCKED_POOL_TOKENS = 1000

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

    assert(Txn.ApplicationArgs[0] == "bootstrap")

    # Ensure the same asset ids are included in Txn.ApplicationArgs and Txn.Assets
    int asset_1_id = btoi(Txn.ApplicationArgs[1])
    int asset_2_id = btoi(Txn.ApplicationArgs[2])
    assert(asset_1_id > asset_2_id)
    assert(asset_1_id == Txn.Assets[0])

    int asset_unit_name_exist
    byte asset_1_unit_name = ""
    byte asset_2_unit_name = "ALGO"

    asset_unit_name_exist, asset_1_unit_name = asset_params_get(AssetUnitName, asset_1_id)
    assert(asset_unit_name_exist)

    if asset_2_id == 0:
        # Asset 2 is Algo
        assert(1 == Txn.NumAssets)
    else:
        assert(asset_2_id == Txn.Assets[1])
        asset_unit_name_exist, asset_2_unit_name = asset_params_get(AssetUnitName, asset_2_id)
        assert(asset_unit_name_exist)
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
    exit(1)
end


block main:
    if Txn.ApplicationID == 0:
        # create app
        app_global_put("fee_setter", Txn.Sender)
        app_global_put("fee_collector", Txn.Sender)
        app_global_put("fee_manager", Txn.Sender)
        exit(1)
    end

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

        # TODO: Should we check rekey in all transactions to protect users?
        assert(Txn.RekeyTo == Global.ZeroAddress)

        switch Txn.ApplicationArgs[0]:
            "add_liquidity": add_liquidity
            "remove_liquidity": remove_liquidity
            "swap": swap
            "claim_fees": claim_fees
            "claim_extra": claim_extra
            "set_fee": set_fee
        end


        block swap:
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
        
            int poolers_fee_amount
            int protocol_fee_amount
            int swap_amount
            int output_amount
            int change = 0
            if mode == 'fixed-input':
                poolers_fee_amount, protocol_fee_amount = calculate_fixed_input_fee_amounts(input_amount)
                swap_amount = input_amount - (poolers_fee_amount + protocol_fee_amount)
                output_amount = calculate_fixed_input_swap(input_supply, output_supply, swap_amount)
                assert(output_amount >= min_output)
            elif mode == 'fixed-output':
                output_amount = min_output
                swap_amount = calculate_fixed_output_swap(input_supply, output_supply, output_amount)
                poolers_fee_amount, protocol_fee_amount = calculate_fixed_output_fee_amounts(swap_amount)
                int total_input_amount = swap_amount + (poolers_fee_amount + protocol_fee_amount)
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
            if issued_pool_tokens == 0:
                # Adding liquidity to the pool for the first time:
                # pool_tokens_out = sqrt(asset_1_amount * asset_2_amount) - LOCKED_POOL_TOKENS
                issued_pool_tokens = btoi(bsqrt(itob(asset_1_amount) b* itob(asset_2_amount)))
                pool_tokens_out = issued_pool_tokens - LOCKED_POOL_TOKENS
                asset_1_reserves = asset_1_amount
                asset_2_reserves = asset_2_amount
            else:
                # Adding liquidity after it already has some liquidity
                int a = btoi((itob(asset_1_amount) b* itob(issued_pool_tokens)) b/ itob(asset_1_reserves))
                int b = btoi((itob(asset_2_amount) b* itob(issued_pool_tokens)) b/ itob(asset_2_reserves))

                # TODO: We can re-use the intermediate results to reduce operations cost
                int remainder_a = btoi((itob(asset_1_amount) b* itob(issued_pool_tokens)) b% itob(asset_1_reserves))
                int remainder_b = btoi((itob(asset_2_amount) b* itob(issued_pool_tokens)) b% itob(asset_2_reserves))

                if a > b:
                    # Less of asset 2 supplied. Pool tokens will be issued proportionally to asset_2_amount (b)
                    # Calculate amount of asset 1 to return to user as change
                    pool_tokens_out = b
                    int expected_asset_1_amount = btoi((itob(pool_tokens_out) b* itob(asset_1_reserves)) b/ itob(issued_pool_tokens))
                    if remainder_b:
                        expected_asset_1_amount = expected_asset_1_amount + 1
                    end
                    int change = asset_1_amount - expected_asset_1_amount
                    asset_1_amount = expected_asset_1_amount
                    transfer_to_user(asset_1_id, change)
                else:
                    # Less of asset 1 supplied. Pool tokens will be issued proportionally to asset_1_amount (a)
                    # Calculate amount of asset 2 to return to user as change
                    pool_tokens_out = a
                    int expected_asset_2_amount = btoi((itob(pool_tokens_out) b* itob(asset_2_reserves)) b/ itob(issued_pool_tokens))
                    if remainder_a:
                        expected_asset_2_amount = expected_asset_2_amount + 1
                    end
                    int change = asset_2_amount - expected_asset_2_amount
                    asset_2_amount = expected_asset_2_amount
                    transfer_to_user(asset_2_id, change)
                end
                issued_pool_tokens = issued_pool_tokens + pool_tokens_out
                asset_1_reserves = asset_1_reserves + asset_1_amount
                asset_2_reserves = asset_2_reserves + asset_2_amount
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
        end
    end
    return
end


func calculate_fixed_input_fee_amounts(input_amount: int) int:
    int poolers_fee_share = app_local_get(1, "poolers_fee_share")
    int protocol_fee_share = app_local_get(1, "protocol_fee_share")
    int poolers_fee = (input_amount * poolers_fee_share) / 10000
    int protocol_fee = (input_amount * protocol_fee_share) / 10000
    return poolers_fee, protocol_fee
end


func calculate_fixed_output_fee_amounts(swap_amount: int) int:
    int poolers_fee_share = app_local_get(1, "poolers_fee_share")
    int protocol_fee_share = app_local_get(1, "protocol_fee_share")
    int input_amount = (swap_amount * 10000) / (10000 - (poolers_fee_share + protocol_fee_share))
    int poolers_fee = (input_amount * poolers_fee_share) / 10000
    int protocol_fee = (input_amount * protocol_fee_share) / 10000
    return poolers_fee, protocol_fee
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
