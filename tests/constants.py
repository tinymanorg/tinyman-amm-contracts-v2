from algojig import TealishProgram
from algosdk.logic import get_application_address
from algosdk.abi import Contract, NetworkInfo

amm_pool_template = TealishProgram('contracts/pool_template.tl')
amm_approval_program = TealishProgram('contracts/amm_approval.tl')
amm_clear_state_program = TealishProgram('contracts/amm_clear_state.tl')

METHOD_BOOTSTRAP = "bootstrap"
METHOD_ADD_LIQUIDITY = "add_liquidity"
METHOD_REMOVE_LIQUIDITY = "remove_liquidity"
METHOD_SWAP = "swap"
METHOD_FLASH = "flash"
METHOD_VERIFY_FLASH = "verify_flash"
METHOD_CLAIM_FEES = "claim_fees"
METHOD_CLAIM_EXTRA = "claim_extra"
METHOD_SET_FEE = "set_fee"
METHOD_SET_FEE_COLLECTOR = "set_fee_collector"
METHOD_SET_FEE_SETTER = "set_fee_setter"
METHOD_SET_FEE_MANAGER = "set_fee_manager"

SWAP_MODE_FIXED_INPUT = "fixed-input"
SWAP_MODE_FIXED_OUTPUT = "fixed-output"


ABI_METHOD = {
    METHOD_BOOTSTRAP: b'\x1dd\x8dm',
    METHOD_ADD_LIQUIDITY: b'E\x1d\x91\xb3',
    METHOD_REMOVE_LIQUIDITY: b'\x10`^T',
    METHOD_SWAP: b"\xba'-\xc5",
    METHOD_FLASH: b'\x9bQA\x1c',
    METHOD_VERIFY_FLASH: b'\xfd\xe6\xc1\xb5',
    METHOD_CLAIM_FEES: b'xb\xe4\xae',
    METHOD_CLAIM_EXTRA: b'z`\x8a\xa5',
    METHOD_SET_FEE: b'\xfe\x11\xcc[',
    # TODO: Add ABI
    # METHOD_SET_FEE_COLLECTOR: "set_fee_collector",
    # METHOD_SET_FEE_SETTER: "set_fee_setter",
    # METHOD_SET_FEE_MANAGER: "set_fee_manager",
}

POOLERS_FEE_SHARE = 25
PROTOCOL_FEE_SHARE = 5
LOCKED_POOL_TOKENS = 1_000
PRICE_SCALE_FACTOR = 2**64      # 18446744073709551616
BLOCK_TIME_DELTA = 1000
BYTE_ZERO = b'\x00\x00\x00\x00\x00\x00\x00\x00'

MAX_UINT64 = 2**64 - 1    # 18446744073709551615
MAX_ASSET_AMOUNT = MAX_UINT64
POOL_TOKEN_TOTAL_SUPPLY = MAX_ASSET_AMOUNT
ALGO_ASSET_ID = 0
APPLICATION_ID = 1
APPLICATION_ADDRESS = get_application_address(APPLICATION_ID)
contract = Contract.from_json(open('contracts/abi.json').read())
contract.networks["algojig"] = NetworkInfo(app_id=APPLICATION_ID)
