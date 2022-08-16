#pragma version 7

const int TINYMAN_APP_ID = 1
const byte TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"

byte pool_cumulative_asset_1_price_key = concat(Txn.Accounts[1], "_cumulative_asset_1_price")
byte pool_cumulative_asset_2_price_key = concat(Txn.Accounts[1], "_cumulative_asset_2_price")
byte pool_cumulative_price_update_timestamp_key = concat(Txn.Accounts[1], "_price_update_timestamp")
byte pool_asset_1_price_key = concat(Txn.Accounts[1], "_asset_1_price")
byte pool_asset_2_price_key = concat(Txn.Accounts[1], "_asset_2_price")

byte cumulative_asset_1_price
byte cumulative_asset_2_price
int cumulative_price_update_timestamp
int exists

exists, cumulative_asset_1_price = app_local_get_ex(1, TINYMAN_APP_ID, "cumulative_asset_1_price")
assert(exists)
exists, cumulative_asset_2_price = app_local_get_ex(1, TINYMAN_APP_ID, "cumulative_asset_2_price")
assert(exists)
exists, cumulative_price_update_timestamp = app_local_get_ex(1, TINYMAN_APP_ID, "cumulative_price_update_timestamp")
assert(exists)

int time_delta = cumulative_price_update_timestamp - app_global_get(pool_cumulative_price_update_timestamp_key)

if time_delta:
    if app_global_get(pool_cumulative_price_update_timestamp_key):
        byte asset_1_price = (cumulative_asset_1_price b- app_global_get(pool_cumulative_asset_1_price_key)) b/ itob(time_delta)
        byte asset_2_price = (cumulative_asset_2_price b- app_global_get(pool_cumulative_asset_2_price_key)) b/ itob(time_delta)
        app_global_put(pool_asset_1_price_key, asset_1_price)
        app_global_put(pool_asset_2_price_key, asset_2_price)
    end

    app_global_put(pool_cumulative_asset_1_price_key, cumulative_asset_1_price)
    app_global_put(pool_cumulative_asset_2_price_key, cumulative_asset_2_price)
    app_global_put(pool_cumulative_price_update_timestamp_key, cumulative_price_update_timestamp)
end
exit(1)