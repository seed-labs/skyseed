#include "skyseed.h"

/* One mutable counter per node, shared by network and local-output ingress. */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
} packets SEC(".maps");

static __always_inline int handle_packet(struct sky_context *ctx)
{
    __u32 key = 0;
    __u64 *count = bpf_map_lookup_elem(&packets, &key);
    if (count)
        __sync_fetch_and_add(count, 1);
    /* sky_is_local(ctx) distinguishes local output when policy needs its origin.
     * A plain return still observes ingress and releases the generation reader.
     * PASS leaves processing to the network stack; it asserts no local delivery. */
    return TC_ACT_OK;
}
SKYSEED_PROGRAM(handle_packet);
