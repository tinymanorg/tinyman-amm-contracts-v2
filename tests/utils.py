from decimal import ROUND_UP, Decimal

from algosdk.future import transaction


def int_to_bytes_without_zero_padding(value):
    length = int((Decimal(value.bit_length()) / 8).quantize(Decimal('1.'), rounding=ROUND_UP))
    return value.to_bytes(length, "big")


def itob(value):
    """ The same as teal itob - int to 8 bytes """
    return value.to_bytes(8, 'big')


def get_pool_logicsig_bytecode(pool_template, app_id, asset_1_id, asset_2_id):
    # These are the bytes of the logicsig template. This needs to be updated if the logicsig is updated.
    program = bytearray(pool_template.bytecode)

    template = b'\x06\x80\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x81\x00[5\x004\x001\x18\x12D1\x19\x81\x01\x12D\x81\x01C'
    assert program == bytearray(template)

    program[3:11] = app_id.to_bytes(8, 'big')
    program[11:19] = asset_1_id.to_bytes(8, 'big')
    program[19:27] = asset_2_id.to_bytes(8, 'big')
    return transaction.LogicSigAccount(program)


def print_logs(txn):
    logs = txn[b'dt'].get(b'lg')
    if logs:
        for log in logs:
            if b'%i' in log:
                i = log.index(b'%i')
                s = log[0:i].decode()
                value = int.from_bytes(log[i + 2:], 'big')
                print(f'{s}: {value}')
