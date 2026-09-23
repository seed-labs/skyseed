/* Private default forwarding profile. Its table encoding is not the public API. */
#define SKY_MANAGED_TABLES 1
#include "skyseed.h"

struct version_key { __u32 version, value; };
SKY_MAP(gen_versions, BPF_MAP_TYPE_HASH, __u32, __u32);
SKY_MAP(active_links, BPF_MAP_TYPE_HASH, struct version_key, __u32);
SKY_MAP(routes, BPF_MAP_TYPE_HASH, struct version_key, __u32);

static __always_inline int default_handler(struct sky_context *ctx)
{
    unsigned char *data = (void *)(long)ctx->skb->data;
    unsigned char *end = (void *)(long)ctx->skb->data_end;
    if (data + 14 > end)
        return sky_result(ctx, TC_ACT_SHOT, SKY_TRUNCATED_L2);
    if (data[12] != 8 || data[13] != 0)
        return sky_result(ctx, TC_ACT_OK, SKY_NON_IPV4);
    if (data + 34 > end)
        return sky_result(ctx, TC_ACT_SHOT, SKY_TRUNCATED_IPV4);
    if (ctx->generation == SKY_UNKNOWN_GENERATION)
        return sky_result(ctx, TC_ACT_SHOT, SKY_NO_GENERATION);
    __u32 *version = bpf_map_lookup_elem(&gen_versions, &ctx->generation);
    if (!version)
        return sky_result(ctx, TC_ACT_SHOT, SKY_NO_GENERATION);
    struct version_key key = { *version, ctx->ingress_port };
    if (!sky_is_local(ctx) && (!ctx->ingress_port || !bpf_map_lookup_elem(&active_links, &key)))
        return sky_result(ctx, TC_ACT_SHOT, SKY_INACTIVE_INGRESS);
    __builtin_memcpy(&key.value, data + 30, 4);
    __u32 *port = bpf_map_lookup_elem(&routes, &key);
    if (!port)
        return sky_result(ctx, TC_ACT_SHOT, SKY_NO_ROUTE);
    if (*port == 0xfffffffe) return sky_drop(ctx);
    if (*port == 0xfffffffd) return sky_pass(ctx);
    if (!*port)
        return sky_result(ctx, TC_ACT_OK, SKY_LOCAL_ROUTE);
    __u32 index = sky_ifindex(*port);
    if (!index)
        return sky_result(ctx, TC_ACT_SHOT, SKY_NO_ROUTE);
    return sky_redirect(ctx, index, 0, SKY_ROUTE_REDIRECT);
}
SKYSEED_PROGRAM(default_handler);
