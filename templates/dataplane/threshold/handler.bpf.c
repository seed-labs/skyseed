#include "threshold.h"

/* Program-owned mutable state: the framework never populates/reclaims it. */
struct { __uint(type, BPF_MAP_TYPE_ARRAY); __uint(max_entries, 1);
    __type(key, __u32); __type(value, __u64); } packets SEC(".maps");

static __always_inline int select_packet(struct sky_context *ctx)
{
    __u32 zero = 0;
    __u64 *count = bpf_map_lookup_elem(&packets, &zero);
    if (count) __sync_fetch_and_add(count, 1);
    unsigned char *data = (void *)(long)ctx->skb->data;
    unsigned char *end = (void *)(long)ctx->skb->data_end;
    if (data + 34 > end || data[12] != 8 || data[13] != 0)
        return sky_pass(ctx);
    if (!sky_data_active(ctx)) return sky_drop(ctx);
    __u32 target;
    __builtin_memcpy(&target, data + 30, 4);
    struct threshold_route *rule = threshold_lookup(ctx, target);
    if (!rule) return sky_drop(ctx);
    if (rule->disposition == SKY_DROP) return sky_drop(ctx);
    if (rule->disposition == SKY_PASS) return sky_pass(ctx);
    /* Only this handler gives threshold its meaning. Weight is recorded but
       deliberately unused: its name does not imply selection policy. */
    __u32 index = rule->count > 1 && ctx->skb->len >= rule->data.threshold ? 1 : 0;
    struct threshold_candidate *candidate = threshold_candidate_at(rule, index);
    return candidate ? sky_forward(ctx, candidate->exit) : sky_drop(ctx);
}
SKYSEED_PROGRAM(select_packet);
