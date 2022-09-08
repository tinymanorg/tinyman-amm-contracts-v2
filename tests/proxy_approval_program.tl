#pragma version 7

const int TINYMAN_APP_ID = 1
const int FEE_BASIS_POINTS = 100

assert(Gtxn[0].AssetReceiver == Global.CurrentApplicationAddress)
int swap_amount = (Gtxn[0].AssetAmount * (10000 - FEE_BASIS_POINTS)) / 10000
int initial_output_balance
_, initial_output_balance = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, Txn.Assets[1])
inner_group:
    inner_txn:
        TypeEnum: Axfer
        Fee: 0
        AssetReceiver: Txn.Accounts[1]
        XferAsset: Gtxn[0].XferAsset
        AssetAmount: swap_amount
    end
    inner_txn:
        TypeEnum: Appl
        Fee: 0
        ApplicationID: TINYMAN_APP_ID
        ApplicationArgs[0]: "swap"
        ApplicationArgs[1]: Txn.ApplicationArgs[1]
        ApplicationArgs[2]: "fixed-input"
        Accounts[0]: Txn.Accounts[1]
        Assets[0]: Txn.Assets[0]
        Assets[1]: Txn.Assets[1]
    end
end

int new_output_balance
_, new_output_balance = asset_holding_get(AssetBalance, Global.CurrentApplicationAddress, Txn.Assets[1])
int output_amount = new_output_balance - initial_output_balance
inner_txn:
    TypeEnum: Axfer
    Fee: 0
    AssetReceiver: Txn.Sender
    XferAsset: Txn.Assets[1]
    AssetAmount: output_amount
end
exit(1)