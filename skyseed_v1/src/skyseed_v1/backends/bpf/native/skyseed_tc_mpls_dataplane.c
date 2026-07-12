// SPDX-License-Identifier: GPL-2.0
/*
 * Skyseed strict BPF-owned MPLS-like dataplane.
 *
 * Edge ingress classifies IP packets into a G/Z FEC and pushes one MPLS
 * unicast shim. Satellite-core hops switch on generation, ifindex, label, and
 * direction only. Edge egress pops the shim before handoff.
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

#ifndef ETH_P_MPLS_UC
#define ETH_P_MPLS_UC 0x8847
#endif

#ifndef SKYSEED_BPF_MAX_MPLS_CLASSIFIERS
#define SKYSEED_BPF_MAX_MPLS_CLASSIFIERS 128
#endif

#ifndef SKYSEED_BPF_MAX_GATEWAY_SELECTORS
#define SKYSEED_BPF_MAX_GATEWAY_SELECTORS 8192
#endif

#ifndef SKYSEED_BPF_MAX_UT_GATEWAY_POLICIES
#define SKYSEED_BPF_MAX_UT_GATEWAY_POLICIES 8192
#endif

#ifndef SKYSEED_BPF_MAX_MPLS_LFIB
#define SKYSEED_BPF_MAX_MPLS_LFIB 256
#endif

#ifndef SKYSEED_BPF_MAX_MPLS_HANDOFFS
#define SKYSEED_BPF_MAX_MPLS_HANDOFFS 128
#endif

#ifndef SKYSEED_BPF_MAX_ZONE_EDGE_DELIVERIES
#define SKYSEED_BPF_MAX_ZONE_EDGE_DELIVERIES 8192
#endif

#ifndef SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES
#define SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES 8192
#endif

#ifndef SKYSEED_BPF_MAX_GENERATION_BASES
#define SKYSEED_BPF_MAX_GENERATION_BASES 8192
#endif

#ifndef SKYSEED_BPF_MAX_MPLS_PACKET_COUNTERS
#define SKYSEED_BPF_MAX_MPLS_PACKET_COUNTERS 23
#endif

#define MPLS_DIRECTION_G_TO_Z 1
#define MPLS_DIRECTION_Z_TO_G 2
#define MPLS_BOS 1
#define MPLS_DEFAULT_TTL 64
#define MPLS_LABEL_MASK 0x000FFFFF

#define ACTION_SWAP 1
#define ACTION_PRESERVE 2
#define ACTION_POP_AND_HANDOFF 3
#define ACTION_POP_AND_ZONE_EDGE_LOOKUP 4

#define COUNTER_CLASSIFIER_HIT 0
#define COUNTER_LABEL_PUSH 1
#define COUNTER_CORE_LFIB_HIT 2
#define COUNTER_LABEL_SWAP 3
#define COUNTER_EGRESS_HANDOFF_HIT 4
#define COUNTER_LABEL_POP 5
#define COUNTER_MISS 6
#define COUNTER_DROP_MISSING_LABEL 7
#define COUNTER_DROP_MISSING_GENERATION 8
#define COUNTER_DROP_BAD_PACKET 9
#define COUNTER_REDIRECT 10
#define COUNTER_PASS 11
#define COUNTER_DROP 12
#define COUNTER_POP_PULL_FAIL 13
#define COUNTER_POP_PROTO_FAIL 14
#define COUNTER_POP_ADJUST_FAIL 15
#define COUNTER_POP_MANUAL_FALLBACK 16
#define COUNTER_POP_MANUAL_FAIL 17
#define COUNTER_NON_GZ_IP_PASS 18
#define COUNTER_UNKNOWN_ROLE 19
#define COUNTER_ROLE_MAP_MISSING 20
#define COUNTER_GZ_IP_PASS_WITHOUT_CLASSIFICATION 21
#define COUNTER_MPLS_SHIM_PASS_WITHOUT_LFIB_OR_HANDOFF 22

#define IFACE_ROLE_CLASSIFIER 1
#define IFACE_ROLE_MPLS 2
#define IFACE_ROLE_UT_GATEWAY_SELECTOR 4

#define MPLS_POP_MANUAL_MAX_SHIFT 128
#define MISS_SAMPLE_SLOT_CLASSIFIER 0
#define MISS_SAMPLE_SLOT_MPLS_LFIB_HANDOFF 1
#define MISS_SAMPLE_SLOT_MISSING_ROLE 2
#define MISS_SAMPLE_SLOT_UNKNOWN_ROLE 3
#define MISS_SAMPLE_SLOT_MISSING_GENERATION 4
#define MISS_SAMPLE_SLOT_BAD_PACKET 5
#define MISS_SAMPLE_SLOT_MISSING_LABEL 6

#define MISS_REASON_CLASSIFIER 1
#define MISS_REASON_MPLS_LFIB_HANDOFF 2
#define MISS_REASON_MISSING_ROLE 3
#define MISS_REASON_UNKNOWN_ROLE 4
#define MISS_REASON_MISSING_GENERATION 5
#define MISS_REASON_BAD_PACKET 6
#define MISS_REASON_MISSING_LABEL 7

#define MISS_MAP_CLASSIFIER 1
#define MISS_MAP_CORE_LFIB 2
#define MISS_MAP_EGRESS_HANDOFF 3
#define MISS_MAP_ACTIVE_GENERATION 4
#define MISS_MAP_INTERFACE_ROLE 5
#define MISS_MAP_PACKET_PARSE 6
#define MISS_MAP_ZONE_EDGE_DELIVERY 7

struct bpf_elf_map {
    __u32 type;
    __u32 size_key;
    __u32 size_value;
    __u32 max_elem;
    __u32 flags;
    __u32 id;
    __u32 pinning;
};

struct ingress_classifier_key {
    __u32 generation;
    __u32 ingress_ifindex;
    __u32 dst_ipv4;
    __u32 service_class;
};

struct ingress_classifier_value {
    __u32 active;
    __u32 fec_id;
    __u32 direction;
    __u32 initial_label;
    __u32 out_ifindex;
    __u32 inner_ethertype;
    unsigned char dst_mac[6];
    unsigned char src_mac[6];
};

struct ut_gateway_policy_key {
    __u32 prefixlen;
    __u32 egress_ifindex;
    __u32 dst_ipv4;
};

struct ut_gateway_policy_value {
    __u32 active;
    __u32 gateway_selector_label;
};

struct lfib_key {
    __u32 generation;
    __u32 ingress_ifindex;
    __u32 label;
    __u32 direction;
};

struct core_lfib_value {
    __u32 active;
    __u32 action;
    __u32 out_label;
    __u32 out_ifindex;
    unsigned char dst_mac[6];
    unsigned char src_mac[6];
};

struct egress_handoff_value {
    __u32 active;
    __u32 action;
    __u32 out_ifindex;
    __u32 inner_ethertype;
    __u32 new_dst_ipv4;
    unsigned char dst_mac[6];
    unsigned char src_mac[6];
};

struct zone_edge_delivery_key {
    __u32 generation;
    __u32 ingress_ifindex;
    __u32 label;
    __u32 dst_ipv4;
};

struct zone_edge_delivery_value {
    __u32 active;
    __u32 out_ifindex;
    __u32 new_dst_ipv4;
    unsigned char dst_mac[6];
    unsigned char src_mac[6];
};

struct generation_base_key {
    __u32 generation;
};

struct generation_base_value {
    __u32 base_generation;
};

struct interface_counter_key {
    __u32 ifindex;
    __u32 counter;
};

struct interface_role_key {
    __u32 ifindex;
};

struct miss_sample {
    __u64 sequence;
    __u32 reason;
    __u32 map_id;
    __u32 generation;
    __u32 active_generation;
    __u32 ifindex;
    __u32 interface_role;
    __u32 direction;
    __u32 ethertype;
    __u32 has_mpls_shim;
    __u32 src_ipv4;
    __u32 dst_ipv4;
    __u32 key_generation;
    __u32 key_ingress_ifindex;
    __u32 key_dst_ipv4;
    __u32 key_service_class;
    __u32 key_label;
    __u32 key_direction;
    __u32 gz_scope;
};

static void *(*bpf_map_lookup_elem)(void *map, const void *key) =
    (void *)BPF_FUNC_map_lookup_elem;
static int (*bpf_map_update_elem)(void *map, const void *key, const void *value,
                                  __u64 flags) = (void *)BPF_FUNC_map_update_elem;
static int (*bpf_skb_store_bytes)(struct __sk_buff *skb, __u32 offset,
                                  const void *from, __u32 len, __u64 flags) =
    (void *)BPF_FUNC_skb_store_bytes;
static int (*bpf_skb_adjust_room)(struct __sk_buff *skb, __s32 len_diff,
                                  __u32 mode, __u64 flags) =
    (void *)BPF_FUNC_skb_adjust_room;
static int (*bpf_skb_pull_data)(struct __sk_buff *skb, __u32 len) =
    (void *)BPF_FUNC_skb_pull_data;
static int (*bpf_skb_load_bytes)(const struct __sk_buff *skb, __u32 offset,
                                 void *to, __u32 len) =
    (void *)BPF_FUNC_skb_load_bytes;
static int (*bpf_skb_change_tail)(struct __sk_buff *skb, __u32 len,
                                  __u64 flags) =
    (void *)BPF_FUNC_skb_change_tail;
static int (*bpf_l3_csum_replace)(struct __sk_buff *skb, __u32 offset,
                                  __u64 from, __u64 to, __u64 size) =
    (void *)BPF_FUNC_l3_csum_replace;
static int (*bpf_redirect)(__u32 ifindex, __u64 flags) =
    (void *)BPF_FUNC_redirect;

struct bpf_elf_map SEC("maps") active_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 1,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") ingress_classifier_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct ingress_classifier_key),
    .size_value = sizeof(struct ingress_classifier_value),
    .max_elem = SKYSEED_BPF_MAX_MPLS_CLASSIFIERS,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") ut_gateway_policy_map = {
    .type = BPF_MAP_TYPE_LPM_TRIE,
    .size_key = sizeof(struct ut_gateway_policy_key),
    .size_value = sizeof(struct ut_gateway_policy_value),
    .max_elem = SKYSEED_BPF_MAX_UT_GATEWAY_POLICIES,
    .flags = BPF_F_NO_PREALLOC,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") gateway_selector_ingress_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct lfib_key),
    .size_value = sizeof(struct core_lfib_value),
    .max_elem = SKYSEED_BPF_MAX_GATEWAY_SELECTORS,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") core_lfib_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct lfib_key),
    .size_value = sizeof(struct core_lfib_value),
    .max_elem = SKYSEED_BPF_MAX_MPLS_LFIB,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") egress_handoff_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct lfib_key),
    .size_value = sizeof(struct egress_handoff_value),
    .max_elem = SKYSEED_BPF_MAX_MPLS_HANDOFFS,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") zone_edge_delivery_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct zone_edge_delivery_key),
    .size_value = sizeof(struct zone_edge_delivery_value),
    .max_elem = SKYSEED_BPF_MAX_ZONE_EDGE_DELIVERIES,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") generation_base_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct generation_base_key),
    .size_value = sizeof(struct generation_base_value),
    .max_elem = SKYSEED_BPF_MAX_GENERATION_BASES,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") packet_counters = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u64),
    .max_elem = SKYSEED_BPF_MAX_MPLS_PACKET_COUNTERS,
    .pinning = PIN_GLOBAL_NS,
};

static __always_inline __u32 planned_base_generation(__u32 generation)
{
    struct generation_base_key key = {
        .generation = generation,
    };
    struct generation_base_value *base = bpf_map_lookup_elem(&generation_base_map, &key);

    if (!base)
        return generation;
    if (base->base_generation == generation)
        return generation;
    return base->base_generation;
}

static __always_inline struct core_lfib_value *
lookup_core_lfib_planned_overlay(struct lfib_key *key)
{
    struct core_lfib_value *current = bpf_map_lookup_elem(&core_lfib_map, key);
    __u32 current_generation = key->generation;
    __u32 base_generation;

    if (current)
        return current;
    base_generation = planned_base_generation(current_generation);
    if (base_generation == current_generation)
        return 0;
    key->generation = base_generation;
    return bpf_map_lookup_elem(&core_lfib_map, key);
}

static __always_inline struct core_lfib_value *
lookup_gateway_selector_planned_overlay(struct lfib_key *key)
{
    struct core_lfib_value *current = bpf_map_lookup_elem(
        &gateway_selector_ingress_map,
        key);
    __u32 current_generation = key->generation;
    __u32 base_generation;

    if (current)
        return current;
    base_generation = planned_base_generation(current_generation);
    if (base_generation == current_generation)
        return 0;
    key->generation = base_generation;
    return bpf_map_lookup_elem(&gateway_selector_ingress_map, key);
}

static __always_inline struct egress_handoff_value *
lookup_egress_handoff_planned_overlay(struct lfib_key *key)
{
    struct egress_handoff_value *current = bpf_map_lookup_elem(&egress_handoff_map, key);
    __u32 current_generation = key->generation;
    __u32 base_generation;

    if (current)
        return current;
    base_generation = planned_base_generation(current_generation);
    if (base_generation == current_generation)
        return 0;
    key->generation = base_generation;
    return bpf_map_lookup_elem(&egress_handoff_map, key);
}

static __always_inline struct zone_edge_delivery_value *
lookup_zone_edge_delivery_planned_overlay(struct zone_edge_delivery_key *key)
{
    struct zone_edge_delivery_value *current = bpf_map_lookup_elem(
        &zone_edge_delivery_map,
        key);
    __u32 current_generation = key->generation;
    __u32 base_generation;

    if (current)
        return current;
    base_generation = planned_base_generation(current_generation);
    if (base_generation == current_generation)
        return 0;
    key->generation = base_generation;
    return bpf_map_lookup_elem(&zone_edge_delivery_map, key);
}

static __always_inline struct ingress_classifier_value *
lookup_classifier_planned_overlay(struct ingress_classifier_key *key)
{
    struct ingress_classifier_value *current = bpf_map_lookup_elem(
        &ingress_classifier_map,
        key);
    __u32 current_generation = key->generation;
    __u32 base_generation;

    if (current)
        return current;
    base_generation = planned_base_generation(current_generation);
    if (base_generation == current_generation)
        return 0;
    key->generation = base_generation;
    return bpf_map_lookup_elem(&ingress_classifier_map, key);
}

struct bpf_elf_map SEC("maps") interface_counters = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct interface_counter_key),
    .size_value = sizeof(__u64),
    .max_elem = SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") interface_role_map = {
    .type = BPF_MAP_TYPE_HASH,
    .size_key = sizeof(struct interface_role_key),
    .size_value = sizeof(__u32),
    .max_elem = SKYSEED_BPF_MAX_INTERFACE_COUNTER_ENTRIES,
    .pinning = PIN_GLOBAL_NS,
};

struct bpf_elf_map SEC("maps") miss_samples = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(struct miss_sample),
    .max_elem = 32,
    .pinning = PIN_GLOBAL_NS,
};

static __always_inline void count_packet(__u32 key)
{
    __u64 *counter = bpf_map_lookup_elem(&packet_counters, &key);

    if (counter)
        __sync_fetch_and_add(counter, 1);
}

static __always_inline void count_interface_packet(__u32 ifindex, __u32 key)
{
    struct interface_counter_key counter_key = {
        .ifindex = ifindex,
        .counter = key,
    };
    __u64 initial = 1;
    __u64 *counter = bpf_map_lookup_elem(&interface_counters, &counter_key);

    if (counter) {
        __sync_fetch_and_add(counter, 1);
        return;
    }
    bpf_map_update_elem(&interface_counters, &counter_key, &initial, BPF_ANY);
}

static __always_inline void record_miss_sample(__u32 slot,
                                               struct miss_sample *sample)
{
    __u32 sequence_key = COUNTER_MISS;
    __u64 *sequence = bpf_map_lookup_elem(&packet_counters, &sequence_key);

    if (sequence)
        sample->sequence = *sequence + 1;
    bpf_map_update_elem(&miss_samples, &slot, sample, BPF_ANY);
}

static __always_inline int rewrite_l2_bytes(struct __sk_buff *skb,
                                            const unsigned char *dst_mac,
                                            const unsigned char *src_mac)
{
    unsigned char rewrite[12];

    if (!dst_mac[0])
        return 0;

    rewrite[0] = dst_mac[0];
    rewrite[1] = dst_mac[1];
    rewrite[2] = dst_mac[2];
    rewrite[3] = dst_mac[3];
    rewrite[4] = dst_mac[4];
    rewrite[5] = dst_mac[5];
    rewrite[6] = src_mac[0];
    rewrite[7] = src_mac[1];
    rewrite[8] = src_mac[2];
    rewrite[9] = src_mac[3];
    rewrite[10] = src_mac[4];
    rewrite[11] = src_mac[5];

    return bpf_skb_store_bytes(skb, 0, rewrite, sizeof(rewrite), 0);
}

static __always_inline int rewrite_dst_ipv4(struct __sk_buff *skb,
                                            __u32 old_dst_ipv4,
                                            __u32 new_dst_ipv4)
{
    if (!new_dst_ipv4)
        return 0;
    if (bpf_l3_csum_replace(skb, 24, old_dst_ipv4, new_dst_ipv4, 4) < 0)
        return -1;
    return bpf_skb_store_bytes(skb, 30, &new_dst_ipv4, sizeof(new_dst_ipv4), 0);
}

static __always_inline int parse_ipv4_addrs(struct __sk_buff *skb,
                                            __u32 *src_ipv4,
                                            __u32 *dst_ipv4)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    struct ethhdr *eth = data;
    struct iphdr *ip;

    if ((void *)(eth + 1) > data_end)
        return -1;
    if (eth->h_proto != __builtin_bswap16(ETH_P_IP))
        return 0;
    ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return -1;
    *src_ipv4 = ip->saddr;
    *dst_ipv4 = ip->daddr;
    return 1;
}

static __always_inline int parse_ipv4_dst(struct __sk_buff *skb, __u32 *dst_ipv4)
{
    __u32 src_ipv4 = 0;
    return parse_ipv4_addrs(skb, &src_ipv4, dst_ipv4);
}

static __always_inline __u32 packet_ethertype(struct __sk_buff *skb)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    struct ethhdr *eth = data;

    if ((void *)(eth + 1) > data_end)
        return 0;
    return __builtin_bswap16(eth->h_proto);
}

static __always_inline int parse_mpls(struct __sk_buff *skb, __u32 *label,
                                      __u32 *direction)
{
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;
    struct ethhdr *eth = data;
    __u32 raw;
    __u32 shim;

    if ((void *)(eth + 1) > data_end)
        return -1;
    if (eth->h_proto != __builtin_bswap16(ETH_P_MPLS_UC))
        return 0;
    if ((void *)eth + sizeof(*eth) + sizeof(raw) > data_end)
        return -1;
    raw = *(__u32 *)((void *)eth + sizeof(*eth));
    shim = __builtin_bswap32(raw);
    if (!((shim >> 8) & MPLS_BOS))
        return -1;
    *label = (shim >> 12) & MPLS_LABEL_MASK;
    *direction = (shim >> 9) & 0x7;
    if (!*label || !*direction)
        return -1;
    return 1;
}

static __always_inline __u32 mpls_shim(__u32 label, __u32 direction)
{
    __u32 shim = ((label & MPLS_LABEL_MASK) << 12) |
                 ((direction & 0x7) << 9) |
                 (MPLS_BOS << 8) |
                 MPLS_DEFAULT_TTL;
    return __builtin_bswap32(shim);
}

static __always_inline int push_mpls(struct __sk_buff *skb, __u32 label,
                                     __u32 direction)
{
    __u16 proto = __builtin_bswap16(ETH_P_MPLS_UC);
    __u32 shim = mpls_shim(label, direction);

    if (bpf_skb_pull_data(skb, sizeof(struct ethhdr)) < 0)
        return -1;
    if (bpf_skb_adjust_room(
            skb,
            sizeof(shim),
            BPF_ADJ_ROOM_MAC,
            BPF_F_ADJ_ROOM_FIXED_GSO) < 0)
        return -1;
    if (bpf_skb_store_bytes(skb, 12, &proto, sizeof(proto), 0) < 0)
        return -1;
    return bpf_skb_store_bytes(skb, sizeof(struct ethhdr), &shim, sizeof(shim), 0);
}

static __always_inline int swap_mpls_label(struct __sk_buff *skb, __u32 label,
                                           __u32 direction)
{
    __u32 shim = mpls_shim(label, direction);

    if (bpf_skb_pull_data(skb, sizeof(struct ethhdr) + sizeof(shim)) < 0)
        return -1;
    return bpf_skb_store_bytes(skb, sizeof(struct ethhdr), &shim, sizeof(shim), 0);
}

static __always_inline int pop_mpls(struct __sk_buff *skb, __u32 inner_ethertype)
{
    __u16 proto = (__u16)inner_ethertype;

    if (bpf_skb_pull_data(skb, sizeof(struct ethhdr) + sizeof(__u32)) < 0)
        return -1;
    proto = __builtin_bswap16(proto);
    if (bpf_skb_store_bytes(skb, 12, &proto, sizeof(proto), 0) < 0)
        return -2;
    if (bpf_skb_adjust_room(
            skb,
            -((__s32)sizeof(__u32)),
            BPF_ADJ_ROOM_MAC,
            BPF_F_ADJ_ROOM_FIXED_GSO) < 0)
        return -3;
    return 0;
}

/*
 * Linux 5.4 rejects the negative BPF_ADJ_ROOM_MAC operation used above for
 * ping-sized frames.  Keep the pop entirely BPF-owned by shifting the inner
 * frame over the shim and trimming the tail.  The strict live smoke and the
 * operational profile both use frames within this deliberately bounded size.
 */
static __always_inline int pop_mpls_manual(struct __sk_buff *skb,
                                           __u32 inner_ethertype)
{
    __u32 old_len = skb->len;
    __u16 proto = (__u16)inner_ethertype;
    unsigned char byte;
    int i;

    if (old_len < sizeof(struct ethhdr) + sizeof(__u32))
        return -1;
    if (old_len > sizeof(struct ethhdr) + sizeof(__u32) + MPLS_POP_MANUAL_MAX_SHIFT)
        return -1;
    proto = __builtin_bswap16(proto);
    if (bpf_skb_store_bytes(skb, 12, &proto, sizeof(proto), 0) < 0)
        return -1;

#pragma unroll
    for (i = 0; i < MPLS_POP_MANUAL_MAX_SHIFT; i++) {
        __u32 from = sizeof(struct ethhdr) + sizeof(__u32) + i;
        __u32 to = sizeof(struct ethhdr) + i;

        if (from >= old_len)
            break;
        if (bpf_skb_load_bytes(skb, from, &byte, sizeof(byte)) < 0)
            return -1;
        if (bpf_skb_store_bytes(skb, to, &byte, sizeof(byte), 0) < 0)
            return -1;
    }
    if (bpf_skb_change_tail(skb, old_len - sizeof(__u32), 0) < 0)
        return -1;
    return 0;
}

SEC("classifier/mpls")
int skyseed_mpls_dataplane(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 src_ipv4 = 0;
    __u32 dst_ipv4 = 0;
    __u32 label = 0;
    __u32 direction = 0;
    __u32 *generation;
    __u32 active_generation_value = 0;
    __u32 interface_role = 0;
    __u32 sample_reason = 0;
    __u32 sample_map_id = 0;
    __u32 sample_slot = 0;
    __u32 sample_key_generation = 0;
    __u32 sample_key_ingress_ifindex = skb->ifindex;
    __u32 sample_key_dst_ipv4 = 0;
    __u32 sample_key_service_class = 0;
    __u32 sample_key_label = 0;
    __u32 sample_key_direction = 0;
    __u32 sample_gz_scope = 0;
    __u32 sample_has_mpls = 0;
    __u32 ethertype = packet_ethertype(skb);
    int mpls_status;
    int ip_status;

    generation = bpf_map_lookup_elem(&active_generation, &zero);
    if (!generation) {
        struct miss_sample sample = {
            .reason = MISS_REASON_MISSING_GENERATION,
            .map_id = MISS_MAP_ACTIVE_GENERATION,
            .ifindex = skb->ifindex,
            .ethertype = ethertype,
        };
        record_miss_sample(MISS_SAMPLE_SLOT_MISSING_GENERATION, &sample);
        count_packet(COUNTER_DROP_MISSING_GENERATION);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }
    active_generation_value = *generation;
    sample_key_generation = active_generation_value;

    mpls_status = parse_mpls(skb, &label, &direction);
    if (mpls_status > 0) {
        struct lfib_key key = {
            .generation = active_generation_value,
            .ingress_ifindex = skb->ifindex,
            .label = label,
            .direction = direction,
        };
        struct core_lfib_value *core;
        sample_has_mpls = 1;
        sample_key_label = label;
        sample_key_direction = direction;
        sample_key_ingress_ifindex = skb->ifindex;
        sample_key_generation = key.generation;
        sample_map_id = MISS_MAP_CORE_LFIB;
        sample_reason = MISS_REASON_MPLS_LFIB_HANDOFF;
        sample_slot = MISS_SAMPLE_SLOT_MPLS_LFIB_HANDOFF;
        sample_gz_scope = 1;

        if (direction == MPLS_DIRECTION_G_TO_Z) {
            struct core_lfib_value *selector =
                lookup_gateway_selector_planned_overlay(&key);
            if (selector && selector->active) {
                if (selector->action != ACTION_SWAP || !selector->out_ifindex)
                    goto bad_packet;
                if (swap_mpls_label(skb, selector->out_label, direction) < 0)
                    goto bad_packet;
                if (rewrite_l2_bytes(skb, selector->dst_mac, selector->src_mac) < 0)
                    goto bad_packet;
                count_packet(COUNTER_LABEL_SWAP);
                count_packet(COUNTER_REDIRECT);
                count_interface_packet(skb->ifindex, COUNTER_REDIRECT);
                return bpf_redirect(selector->out_ifindex, 0);
            }
            key.generation = active_generation_value;
        }
        core = lookup_core_lfib_planned_overlay(&key);

        if (core && core->active) {
            count_packet(COUNTER_CORE_LFIB_HIT);
            count_interface_packet(skb->ifindex, COUNTER_CORE_LFIB_HIT);
            if (core->action == ACTION_SWAP) {
                if (swap_mpls_label(skb, core->out_label, direction) < 0)
                    goto bad_packet;
                count_packet(COUNTER_LABEL_SWAP);
            }
            if (rewrite_l2_bytes(skb, core->dst_mac, core->src_mac) < 0)
                goto bad_packet;
            if (!core->out_ifindex)
                goto miss;
            count_packet(COUNTER_REDIRECT);
            count_interface_packet(skb->ifindex, COUNTER_REDIRECT);
            return bpf_redirect(core->out_ifindex, 0);
        }

        {
            struct egress_handoff_value *handoff;
            key.generation = active_generation_value;
            handoff = lookup_egress_handoff_planned_overlay(&key);
            sample_key_generation = key.generation;
            if (!handoff || !handoff->active) {
                sample_map_id = MISS_MAP_EGRESS_HANDOFF;
                goto miss;
            }
            count_packet(COUNTER_EGRESS_HANDOFF_HIT);
            count_interface_packet(skb->ifindex, COUNTER_EGRESS_HANDOFF_HIT);
            {
                int pop_status = pop_mpls(skb, handoff->inner_ethertype);
                if (pop_status == -1)
                    count_packet(COUNTER_POP_PULL_FAIL);
                if (pop_status == -2)
                    count_packet(COUNTER_POP_PROTO_FAIL);
                if (pop_status == -3)
                    count_packet(COUNTER_POP_ADJUST_FAIL);
                if (pop_status < 0) {
                    if (pop_mpls_manual(skb, handoff->inner_ethertype) < 0) {
                        count_packet(COUNTER_POP_MANUAL_FAIL);
                        goto bad_packet;
                    }
                    count_packet(COUNTER_POP_MANUAL_FALLBACK);
                }
            }
            count_packet(COUNTER_LABEL_POP);
            if (handoff->action == ACTION_POP_AND_ZONE_EDGE_LOOKUP) {
                struct zone_edge_delivery_key delivery_key;
                struct zone_edge_delivery_value *delivery;

                if (direction != MPLS_DIRECTION_Z_TO_G)
                    goto bad_packet;
                ip_status = parse_ipv4_dst(skb, &dst_ipv4);
                if (ip_status <= 0)
                    goto bad_packet;
                delivery_key.generation = active_generation_value;
                delivery_key.ingress_ifindex = skb->ifindex;
                delivery_key.label = label;
                delivery_key.dst_ipv4 = dst_ipv4;
                delivery = lookup_zone_edge_delivery_planned_overlay(&delivery_key);
                sample_key_generation = delivery_key.generation;
                sample_key_dst_ipv4 = dst_ipv4;
                sample_map_id = MISS_MAP_ZONE_EDGE_DELIVERY;
                if (!delivery || !delivery->active || !delivery->out_ifindex)
                    goto miss;
                if (delivery->new_dst_ipv4 &&
                    rewrite_dst_ipv4(skb, dst_ipv4, delivery->new_dst_ipv4) < 0)
                    goto bad_packet;
                if (rewrite_l2_bytes(skb, delivery->dst_mac, delivery->src_mac) < 0)
                    goto bad_packet;
                count_packet(COUNTER_REDIRECT);
                count_interface_packet(skb->ifindex, COUNTER_REDIRECT);
                return bpf_redirect(delivery->out_ifindex, 0);
            }
            if (handoff->new_dst_ipv4) {
                ip_status = parse_ipv4_dst(skb, &dst_ipv4);
                if (ip_status < 0)
                    goto bad_packet;
                if (ip_status > 0 &&
                    rewrite_dst_ipv4(skb, dst_ipv4, handoff->new_dst_ipv4) < 0)
                    goto bad_packet;
            }
            if (rewrite_l2_bytes(skb, handoff->dst_mac, handoff->src_mac) < 0)
                goto bad_packet;
            if (handoff->out_ifindex) {
                count_packet(COUNTER_REDIRECT);
                count_interface_packet(skb->ifindex, COUNTER_REDIRECT);
                return bpf_redirect(handoff->out_ifindex, 0);
            }
            count_packet(COUNTER_PASS);
            return TC_ACT_OK;
        }
    }
    if (mpls_status < 0) {
        struct miss_sample sample = {
            .reason = MISS_REASON_MISSING_LABEL,
            .map_id = MISS_MAP_PACKET_PARSE,
            .generation = active_generation_value,
            .active_generation = active_generation_value,
            .ifindex = skb->ifindex,
            .ethertype = ethertype,
            .has_mpls_shim = 1,
            .gz_scope = 1,
        };
        record_miss_sample(MISS_SAMPLE_SLOT_MISSING_LABEL, &sample);
        count_packet(COUNTER_DROP_MISSING_LABEL);
        count_packet(COUNTER_DROP);
        return TC_ACT_SHOT;
    }

    ip_status = parse_ipv4_addrs(skb, &src_ipv4, &dst_ipv4);
    if (ip_status == 0) {
        count_packet(COUNTER_PASS);
        return TC_ACT_OK;
    }
    if (ip_status < 0)
        goto bad_packet;

    {
        struct interface_role_key role_key = {
            .ifindex = skb->ifindex,
        };
        __u32 *role = bpf_map_lookup_elem(&interface_role_map, &role_key);
        struct ingress_classifier_key key = {
            .generation = active_generation_value,
            .ingress_ifindex = skb->ifindex,
            .dst_ipv4 = dst_ipv4,
            .service_class = 0,
        };
        struct ingress_classifier_value *classifier;

        sample_key_generation = key.generation;
        sample_key_ingress_ifindex = key.ingress_ifindex;
        sample_key_dst_ipv4 = key.dst_ipv4;
        sample_key_service_class = key.service_class;
        sample_map_id = MISS_MAP_CLASSIFIER;
        sample_reason = MISS_REASON_CLASSIFIER;
        sample_slot = MISS_SAMPLE_SLOT_CLASSIFIER;
        sample_gz_scope = 1;
        if (role)
            interface_role = *role;

        if (role && (*role & ~(IFACE_ROLE_CLASSIFIER | IFACE_ROLE_MPLS))) {
            if (*role == IFACE_ROLE_UT_GATEWAY_SELECTOR) {
                struct ut_gateway_policy_key policy_key = {
                    .prefixlen = 64,
                    .egress_ifindex = skb->ifindex,
                    .dst_ipv4 = dst_ipv4,
                };
                struct ut_gateway_policy_value *policy = bpf_map_lookup_elem(
                    &ut_gateway_policy_map,
                    &policy_key);
                if (!policy || !policy->active || !policy->gateway_selector_label)
                    goto miss;
                if (push_mpls(
                        skb,
                        policy->gateway_selector_label,
                        MPLS_DIRECTION_G_TO_Z) < 0)
                    goto bad_packet;
                count_packet(COUNTER_LABEL_PUSH);
                count_packet(COUNTER_PASS);
                return TC_ACT_OK;
            }
            count_packet(COUNTER_UNKNOWN_ROLE);
            sample_reason = MISS_REASON_UNKNOWN_ROLE;
            sample_map_id = MISS_MAP_INTERFACE_ROLE;
            sample_slot = MISS_SAMPLE_SLOT_UNKNOWN_ROLE;
            goto miss;
        }
        if (!role) {
            count_packet(COUNTER_ROLE_MAP_MISSING);
            sample_reason = MISS_REASON_MISSING_ROLE;
            sample_map_id = MISS_MAP_INTERFACE_ROLE;
            sample_slot = MISS_SAMPLE_SLOT_MISSING_ROLE;
            goto miss;
        } else if (!(*role & IFACE_ROLE_CLASSIFIER)) {
            if (*role & IFACE_ROLE_MPLS) {
                count_packet(COUNTER_NON_GZ_IP_PASS);
                count_packet(COUNTER_PASS);
                return TC_ACT_OK;
            }
            count_packet(COUNTER_UNKNOWN_ROLE);
            sample_reason = MISS_REASON_UNKNOWN_ROLE;
            sample_map_id = MISS_MAP_INTERFACE_ROLE;
            sample_slot = MISS_SAMPLE_SLOT_UNKNOWN_ROLE;
            goto miss;
        }
        classifier = lookup_classifier_planned_overlay(&key);
        sample_key_generation = key.generation;
        if (!classifier || !classifier->active)
            goto miss;
        count_packet(COUNTER_CLASSIFIER_HIT);
        count_interface_packet(skb->ifindex, COUNTER_CLASSIFIER_HIT);
        if (push_mpls(skb, classifier->initial_label, classifier->direction) < 0)
            goto bad_packet;
        count_packet(COUNTER_LABEL_PUSH);
        if (rewrite_l2_bytes(skb, classifier->dst_mac, classifier->src_mac) < 0)
            goto bad_packet;
        if (!classifier->out_ifindex)
            goto miss;
        if (classifier->out_ifindex == skb->ifindex) {
            count_packet(COUNTER_PASS);
            return TC_ACT_OK;
        }
        count_packet(COUNTER_REDIRECT);
        count_interface_packet(skb->ifindex, COUNTER_REDIRECT);
        return bpf_redirect(classifier->out_ifindex, 0);
    }

miss:
    {
        struct miss_sample sample = {
            .reason = sample_reason,
            .map_id = sample_map_id,
            .generation = active_generation_value,
            .active_generation = active_generation_value,
            .ifindex = skb->ifindex,
            .interface_role = interface_role,
            .direction = direction,
            .ethertype = ethertype,
            .has_mpls_shim = sample_has_mpls,
            .src_ipv4 = src_ipv4,
            .dst_ipv4 = dst_ipv4,
            .key_generation = sample_key_generation,
            .key_ingress_ifindex = sample_key_ingress_ifindex,
            .key_dst_ipv4 = sample_key_dst_ipv4,
            .key_service_class = sample_key_service_class,
            .key_label = sample_key_label,
            .key_direction = sample_key_direction,
            .gz_scope = sample_gz_scope,
        };
        record_miss_sample(sample_slot, &sample);
    }
    count_packet(COUNTER_MISS);
    count_packet(COUNTER_DROP);
    return TC_ACT_SHOT;

bad_packet:
    {
        struct miss_sample sample = {
            .reason = MISS_REASON_BAD_PACKET,
            .map_id = MISS_MAP_PACKET_PARSE,
            .generation = active_generation_value,
            .active_generation = active_generation_value,
            .ifindex = skb->ifindex,
            .interface_role = interface_role,
            .direction = direction,
            .ethertype = ethertype,
            .has_mpls_shim = sample_has_mpls,
            .src_ipv4 = src_ipv4,
            .dst_ipv4 = dst_ipv4,
            .key_generation = sample_key_generation,
            .key_ingress_ifindex = sample_key_ingress_ifindex,
            .key_dst_ipv4 = sample_key_dst_ipv4,
            .key_service_class = sample_key_service_class,
            .key_label = sample_key_label,
            .key_direction = sample_key_direction,
            .gz_scope = sample_gz_scope,
        };
        record_miss_sample(MISS_SAMPLE_SLOT_BAD_PACKET, &sample);
    }
    count_packet(COUNTER_DROP_BAD_PACKET);
    count_packet(COUNTER_DROP);
    return TC_ACT_SHOT;
}

char __license[] SEC("license") = "GPL";
