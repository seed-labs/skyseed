/* SkySEED TC contract v2. Include in one translation unit per linked object. */
#ifndef SKYSEED_H
#define SKYSEED_H
#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define SKY_LOCAL_INGRESS ((__u32)-1)
#define SKY_UNKNOWN_GENERATION ((__u32)-1)
#define SKY_MAP(name, kind, key_t, value_t) \
    struct { __uint(type, kind); __uint(max_entries, 1); \
        __type(key, key_t); __type(value, value_t); } name SEC(".maps")

SKY_MAP(active_gen, BPF_MAP_TYPE_ARRAY, __u32, __u32);
SKY_MAP(gen_readers, BPF_MAP_TYPE_HASH, __u32, __u64);
SKY_MAP(ingress_bind, BPF_MAP_TYPE_HASH, __u32, __u32);
SKY_MAP(egress_bind, BPF_MAP_TYPE_HASH, __u32, __u32);
SKY_MAP(traffic_losses, BPF_MAP_TYPE_ARRAY, __u32, __u64);
struct { __uint(type, BPF_MAP_TYPE_RINGBUF); __uint(max_entries, 1 << 20); }
    traffic_events SEC(".maps");

/* Framework-owned configuration, bound before libbpf loads the object. */
struct sky_configuration {
    __u32 abi, managed_tables, node_slot, telemetry, continuation;
};
#ifndef SKY_MANAGED_TABLES
#define SKY_MANAGED_TABLES 0
#endif
const volatile struct sky_configuration sky_config SEC(".rodata.skyseed") = {
    2, SKY_MANAGED_TABLES, 0, 1, 1
};

/* Only result_* fields are handler metadata. Other fields are framework facts. */
struct sky_context {
    struct __sk_buff *skb;
    __u32 generation;
    __u32 ingress_port;
    __u32 result_ifindex;
    __u8 result_reason;
};

/* Local origin is infrastructure, not an active network port or exit. */
static __always_inline int sky_is_local(struct sky_context *ctx)
{
    return ctx->ingress_port == SKY_LOCAL_INGRESS;
}

/* Reasons 1..8 describe the optional default handler; 0 means unknown. */
enum sky_reason {
    SKY_UNKNOWN = 0, SKY_ROUTE_REDIRECT = 1, SKY_LOCAL_ROUTE = 2,
    SKY_NON_IPV4 = 3, SKY_TRUNCATED_L2 = 4, SKY_TRUNCATED_IPV4 = 5,
    SKY_NO_GENERATION = 6, SKY_INACTIVE_INGRESS = 7, SKY_NO_ROUTE = 8
};

static __always_inline int sky_result(struct sky_context *ctx, int action, __u8 reason)
{
    ctx->result_reason = reason;
    return action;
}

static __always_inline int sky_redirect(struct sky_context *ctx, __u32 ifindex, __u64 flags, __u8 reason)
{
    ctx->result_ifindex = ifindex;
    ctx->result_reason = reason;
    return bpf_redirect(ifindex, flags);
}

/* Logical ports remain valid across interface incarnations; zero is unresolved. */
static __always_inline __u32 sky_ifindex(__u32 port)
{
    if (!port || port > 0xfffffffc) return 0;
    __u32 *index = bpf_map_lookup_elem(&egress_bind, &port);
    return index ? *index : 0;
}

/* Forward via a logical exit; live binding is resolved at packet time. */
static __always_inline int sky_forward(struct sky_context *ctx, __u32 exit)
{
    __u32 index = sky_ifindex(exit);
    if (!index) return sky_result(ctx, TC_ACT_SHOT, SKY_NO_ROUTE);
    return sky_redirect(ctx, index, 0, SKY_ROUTE_REDIRECT);
}

static __always_inline int sky_pass(struct sky_context *ctx)
{
    return sky_result(ctx, TC_ACT_OK, SKY_UNKNOWN);
}

static __always_inline int sky_drop(struct sky_context *ctx)
{
    return sky_result(ctx, TC_ACT_SHOT, SKY_UNKNOWN);
}

struct sky_event {
    __u64 timestamp_ns;
    __u32 node_slot, generation, ingress_ifindex, egress_ifindex;
    __u32 source_ipv4, destination_ipv4, packet_length;
    __u8 decision, reason, protocol, icmp_valid, icmp_type, icmp_code;
    __u16 icmp_identifier, icmp_sequence, padding;
    __s32 tc_action;
    __u32 facts; /* bit 0: IPv4 observed; bit 1: local output origin */
};
_Static_assert(sizeof(struct sky_event) == 56, "telemetry ABI");

static __always_inline void sky_observe(struct __sk_buff *skb, struct sky_event *event)
{
    event->timestamp_ns = bpf_ktime_get_ns();
    event->node_slot = sky_config.node_slot;
    event->ingress_ifindex = skb->ifindex;
    event->packet_length = skb->len;
    unsigned char *data = (void *)(long)skb->data;
    unsigned char *end = (void *)(long)skb->data_end;
    if (data + 34 > end || data[12] != 8 || data[13] != 0)
        return;
    __builtin_memcpy(&event->source_ipv4, data + 26, 4);
    __builtin_memcpy(&event->destination_ipv4, data + 30, 4);
    event->protocol = data[23];
    event->facts = 1;
    __u32 ihl = data[14] & 15;
    if (event->protocol != 1 || ihl < 5)
        return;
    unsigned char *icmp = data + 14 + ihl * 4;
    if (icmp + 8 > end)
        return;
    event->icmp_type = icmp[0];
    event->icmp_code = icmp[1];
    __builtin_memcpy(&event->icmp_identifier, icmp + 4, 2);
    __builtin_memcpy(&event->icmp_sequence, icmp + 6, 2);
    event->icmp_valid = 1;
}

static __always_inline __u64 *sky_acquire(struct sky_context *ctx)
{
    __u32 zero = 0;
    volatile __u32 *publication = bpf_map_lookup_elem(&active_gen, &zero);
    if (!publication)
        return 0;
    /* Pin, then recheck publication before touching generation data. */
    for (int attempt = 0; attempt < 3; ++attempt) {
        __u32 generation = *publication;
        if (!sky_config.continuation) {
            ctx->generation = generation;
            return 0;
        }
        __u64 *reader = bpf_map_lookup_elem(&gen_readers, &generation);
        if (!reader)
            continue;
        __sync_fetch_and_add(reader, 1);
        if (*publication == generation) {
            ctx->generation = generation;
            return reader;
        }
        __sync_fetch_and_add(reader, -1);
    }
    return 0;
}

/* Normal C returns, including early returns, converge here. A successful tail
 * call or other non-returning transfer bypasses completion and is unsupported. */
#define SKYSEED_PROGRAM(handler) \
    SEC("tc") int skyseed_tc(struct __sk_buff *skb) { \
        struct sky_context ctx = { .skb = skb, .generation = SKY_UNKNOWN_GENERATION }; \
        struct sky_event event = {}; \
        if (sky_config.telemetry) sky_observe(skb, &event); \
        __u64 *reader = sky_acquire(&ctx); \
        __u32 index = skb->ifindex; \
        __u32 *port = bpf_map_lookup_elem(&ingress_bind, &index); \
        if (port) ctx.ingress_port = *port; \
        event.generation = ctx.generation; \
        if (sky_is_local(&ctx)) event.facts |= 2; \
        int action = handler(&ctx); \
        if (sky_config.telemetry) { \
            event.tc_action = action; \
            event.decision = action == TC_ACT_REDIRECT ? 1 : action == TC_ACT_OK ? 2 : action == TC_ACT_SHOT ? 3 : 0; \
            event.reason = ctx.result_reason; \
            event.egress_ifindex = ctx.result_ifindex; \
            if (bpf_ringbuf_output(&traffic_events, &event, sizeof(event), 0) < 0) { \
                __u32 slot = sky_config.node_slot; \
                __u64 *loss = bpf_map_lookup_elem(&traffic_losses, &slot); \
                if (loss) __sync_fetch_and_add(loss, 1); \
            } \
        } \
        if (reader) __sync_fetch_and_add(reader, -1); \
        return action; \
    } \
    char sky_license[] SEC("license") = "GPL"
#endif
