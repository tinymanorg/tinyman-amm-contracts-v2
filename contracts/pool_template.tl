#pragma version 6
const bytes KEY = "\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
int application_id = extract_uint64(KEY, 0)
# Only allow OptIn calls to a specific application id
assert(application_id == Txn.ApplicationID)
assert(Txn.OnCompletion == OptIn)
exit(1)
