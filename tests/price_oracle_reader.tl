#pragma version 7

const int TINYMAN_APP_ID = 1
const bytes TWO_TO_THE_64 = "\x01\x00\x00\x00\x00\x00\x00\x00\x00"

bytes pool_asset_1_cumulative_price_key = concat(Txn.Accounts[1], "_asset_1_cumulative_price")
bytes pool_asset_2_cumulative_price_key = concat(Txn.Accounts[1], "_asset_2_cumulative_price")
bytes pool_cumulative_price_update_timestamp_key = concat(Txn.Accounts[1], "_price_update_timestamp")
bytes pool_asset_1_price_key = concat(Txn.Accounts[1], "_asset_1_price")
bytes pool_asset_2_price_key = concat(Txn.Accounts[1], "_asset_2_price")

bytes asset_1_cumulative_price
bytes asset_2_cumulative_price
int cumulative_price_update_timestamp
int exists

exists, asset_1_cumulative_price = app_local_get_ex(1, TINYMAN_APP_ID, "asset_1_cumulative_price")
assert(exists)
exists, asset_2_cumulative_price = app_local_get_ex(1, TINYMAN_APP_ID, "asset_2_cumulative_price")
assert(exists)
exists, cumulative_price_update_timestamp = app_local_get_ex(1, TINYMAN_APP_ID, "cumulative_price_update_timestamp")
assert(exists)

int time_delta = cumulative_price_update_timestamp - app_global_get(pool_cumulative_price_update_timestamp_key)

if time_delta:
    if app_global_get(pool_cumulative_price_update_timestamp_key):
        bytes asset_1_price = (asset_1_cumulative_price b- app_global_get(pool_asset_1_cumulative_price_key)) b/ itob(time_delta)
        bytes asset_2_price = (asset_2_cumulative_price b- app_global_get(pool_asset_2_cumulative_price_key)) b/ itob(time_delta)
        app_global_put(pool_asset_1_price_key, asset_1_price)
        app_global_put(pool_asset_2_price_key, asset_2_price)
    end

    app_global_put(pool_asset_1_cumulative_price_key, asset_1_cumulative_price)
    app_global_put(pool_asset_2_cumulative_price_key, asset_2_cumulative_price)
    app_global_put(pool_cumulative_price_update_timestamp_key, cumulative_price_update_timestamp)
end
exit(1)