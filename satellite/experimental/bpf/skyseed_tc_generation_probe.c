// SPDX-License-Identifier: GPL-2.0
/*
 * Experimental tc-BPF generation probe for Skyseed feasibility only.
 *
 * This file is intentionally outside the canonical runtime. It is meant for
 * isolated netns/veth experiments that test whether an active_generation map
 * can select pass/drop policy without exposing inactive future state.
 */

#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#include <linux/types.h>

#define SEC(NAME) __attribute__((section(NAME), used))

struct bpf_elf_map {
    __u32 type;
    __u32 size_key;
    __u32 size_value;
    __u32 max_elem;
    __u32 flags;
    __u32 id;
    __u32 pinning;
};

static void *(*bpf_map_lookup_elem)(void *map, const void *key) = (void *)BPF_FUNC_map_lookup_elem;

struct bpf_elf_map SEC("maps") active_generation = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 1,
};

struct bpf_elf_map SEC("maps") generation_policy = {
    .type = BPF_MAP_TYPE_ARRAY,
    .size_key = sizeof(__u32),
    .size_value = sizeof(__u32),
    .max_elem = 16,
};

SEC("classifier")
int skyseed_generation_classifier(struct __sk_buff *skb)
{
    __u32 zero = 0;
    __u32 *generation = bpf_map_lookup_elem(&active_generation, &zero);
    __u32 *policy;

    if (!generation)
        return TC_ACT_SHOT;
    policy = bpf_map_lookup_elem(&generation_policy, generation);
    if (!policy)
        return TC_ACT_SHOT;
    return *policy ? TC_ACT_OK : TC_ACT_SHOT;
}

char __license[] SEC("license") = "GPL";
