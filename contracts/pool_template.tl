#pragma version 6
#tealish version git+https://github.com/tinymanorg/tealish.git@0cec751154b0083c2cb79da43b40aa26b367ecc4

# Tinyman AMM V2
# License: https://github.com/tinymanorg/tinyman-amm-contracts-v2/blob/main/LICENSE
# Documentation: https://docs.tinyman.org

const bytes KEY = "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
int application_id = extract_uint64(KEY, 0)
# Only allow OptIn calls to a specific application id
assert(application_id == Txn.ApplicationID)
assert(Txn.OnCompletion == OptIn)
exit(1)
