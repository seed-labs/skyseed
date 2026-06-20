// SPDX-License-Identifier: GPL-2.0
/*
 * Isolated tc-BPF G/Z forwarding shadow prototype for Skyseed.
 *
 * This is not canonical runtime code. It is only for netns/veth experiments
 * that model RuntimeEpoch active-generation selection as BPF map lookups.
 */

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/pkt_cls.h>
#include <linux/types.h>

#define SEC(NAME) __attribute__((section(NAME), used))
#define PIN_GLOBAL_NS 2

#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif

#define ACTION_FORWARD 1
#define ACTION_LOCAL 2

#define FEC_G 1
#define FEC_Z 2
#define NEXT_HOP_GW1 1
#define NEXT_HOP_GW2 2

#define COUNTER_GENERATION_HIT 0
#define COUNTER_FEC_HIT 1
#define COUNTER_REDIRECT_PASS 2
#define COUNTER_DROP_MISSING_GENERATION 3
#define COUNTER_DROP_MISSING_FEC 4
#define COUNTER_DROP_INACTIVE 5
#define COUNTER_FUTURE_GENERATION_NOT_USED 6
#define COUNTER_LOCAL_DELIVERY 7
#define COUNTER_DROP_BAD_PACKET 8
#define COUNTER_NEXT_HOP_GW1 9
#define COUNTER_NEXT_HOP_GW2 10
#define COUNTER_SHADOW_PASS 11

#define IP4_LE(a, b, c, d) \
    ((__u32)(a) | ((__u32)(b) << 8) | ((__u32)(c) << 16) | ((__u32)(d) << 24))
#define FEC_G_IP IP4_LE(10, 91, 0, 100)
#define FEC_Z_IP IP4_LE(10, 91, 0, 200)

struct bpf_elf_map {
    __u32 type;
    __u32 size_key;
    __u32 size_value;
    __u32 max_elem;
    __u32 flags;
    __u32 id;
    __u32 pinning;
};

struct route_key {
    __u32 generation;
    __u32 fec;
};

struct route_value {
    __u32 active;
    __u32 action;
    __u32 ifindex;
    __u32 next_hop;
    unsigned char dst_mac[6];
    unsigned char src_mac[6];
};

static void *(*bpf_map_lookup_elem)(void *map, const void *key) =
    (void *)BPF_FUNC_map_lookup_elem;
static int (*bpf_skb_store_bytes)(struct __sk_buff *skb, __u32 offset, const void *from,
                                  __u32 len, __u64 flags) =
    (void *)BPF_FUNC_skb_store_bytes;
static int (*bpf_redirect)(__u32 ifindex, __u64 flags) = (void *)BPF_FUNC_redirect;

struct bpf_elf_map SEC("maps") active_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 1,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") generation_status = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 8,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") fec_forwarding = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct route_key),
    .size_value = sizeof(struct route_value),
    .max_elem = 64,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") packet_counters = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u64),
    .max_elem = 16,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") future_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 2,
    .pinning = PIN_GLOBAL_NS,
};

static __always_inline void count_packet(__u32 key)
{
    __u64 *counter = bpf_map_lookup_elem(&packet_counters, &key);

    if (counter)
        __sync_fetch_and_add(counter, 1);
}

static __always_inline int parse_fec(struct __sk_buff *skb, __u32 *fec)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    struct ethhdr *eth = data;
    struct iphdr *ip;

    if ((void *)(eth + 1) > data_end) {
        count_packet(COUNTER_DROP_BAD_PACKET);
        return -1;
    }

    if (eth->h_proto != __builtin_bswap16(ETH_P_IP))
        return 0;

    ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) {
        count_packet(COUNTER_DROP_BAD_PACKET);
        return -1;
    }

    if (ip->daddr == FEC_G_IP) {
        *fec = FEC_G;
        return 1;
    }
    if (ip->daddr == FEC_Z_IP) {
        *fec = FEC_Z;
        return 1;
    }

    count_packet(COUNTER_DROP_MISSING_FEC);
    return -1;
}

static __always_inline int rewrite_l2(struct __sk_buff *skb, struct route_value *route)
{
    unsigned char rewrite[12];

    rewrite[0] = route->dst_mac[0];
    rewrite[1] = route->dst_mac[1];
    rewrite[2] = route->dst_mac[2];
    rewrite[3] = route->dst_mac[3];
    rewrite[4] = route->dst_mac[4];
    rewrite[5] = route->dst_mac[5];
    rewrite[6] = route->src_mac[0];
    rewrite[7] = route->src_mac[1];
    rewrite[8] = route->src_mac[2];
    rewrite[9] = route->src_mac[3];
    rewrite[10] = route->src_mac[4];
    rewrite[11] = route->src_mac[5];

    return bpf_skb_store_bytes(skb, 0, rewrite, sizeof(rewrite), 0);
}

static __always_inline int gz_decide(struct __sk_buff *skb, int do_redirect)
{
    __u32 zero = 0;
    __u32 fec = 0;
    __u32 *generation = bpf_map_lookup_elem(&active_generation, &zero);
    __u32 *generation_ok;
    __u32 *future_generation_value;
    struct route_key key = {};
    struct route_value *route;
    int fec_status;

    if (!generation) {
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        return TC_ACT_SHOT;
    }

    fec_status = parse_fec(skb, &fec);
    if (fec_status == 0)
        return TC_ACT_OK;
    if (fec_status < 0)
        return TC_ACT_SHOT;

    generation_ok = bpf_map_lookup_elem(&generation_status, generation);
    if (!generation_ok || !*generation_ok) {
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        return TC_ACT_SHOT;
    }
    count_packet(COUNTER_GENERATION_HIT);

    future_generation_value = bpf_map_lookup_elem(&future_generation, &zero);
    if (future_generation_value && *future_generation_value != *generation)
        count_packet(COUNTER_FUTURE_GENERATION_NOT_USED);

    count_packet(COUNTER_FEC_HIT);
    key.generation = *generation;
    key.fec = fec;
    route = bpf_map_lookup_elem(&fec_forwarding, &key);
    if (!route || !route->active) {
        count_packet(COUNTER_DROP_INACTIVE);
        return TC_ACT_SHOT;
    }

    if (route->action == ACTION_LOCAL) {
        count_packet(COUNTER_LOCAL_DELIVERY);
        count_packet(COUNTER_REDIRECT_PASS);
        return TC_ACT_OK;
    }
    if (route->action != ACTION_FORWARD || !route->ifindex) {
        count_packet(COUNTER_DROP_INACTIVE);
        return TC_ACT_SHOT;
    }

    if (route->next_hop == NEXT_HOP_GW1)
        count_packet(COUNTER_NEXT_HOP_GW1);
    else if (route->next_hop == NEXT_HOP_GW2)
        count_packet(COUNTER_NEXT_HOP_GW2);

    count_packet(COUNTER_REDIRECT_PASS);
    if (!do_redirect) {
        count_packet(COUNTER_SHADOW_PASS);
        return TC_ACT_OK;
    }

    if (rewrite_l2(skb, route) < 0) {
        count_packet(COUNTER_DROP_BAD_PACKET);
        return TC_ACT_SHOT;
    }
    return bpf_redirect(route->ifindex, 0);
}

SEC("classifier/shadow")
int skyseed_gz_shadow(struct __sk_buff *skb)
{
    return gz_decide(skb, 0);
}

SEC("classifier/redirect")
int skyseed_gz_redirect(struct __sk_buff *skb)
{
    return gz_decide(skb, 1);
}

char __license[] SEC("license") = "GPL";
